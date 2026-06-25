# ============================================================
# app/router_meteo.py — Route principale GET /meteo
# ============================================================
# Ce fichier orchestre tout le travail :
#   1. Vérifie le cache long → si hit, répond immédiatement
#   2. Vérifie le cache court par fournisseur
#   3. Lance les appels manquants en PARALLÈLE (asyncio.gather)
#   4. Filtre les échecs via le circuit breaker
#   5. Fusionne les résultats (moyennes pondérées)
#   6. Écrit dans le cache et retourne la réponse
#
# Concept clé (ch.06 exemple_01_async_route) :
#   asyncio.gather() lance toutes les coroutines simultanément.
#   Si un provider met 500ms, les 3 ensemble mettent ~500ms (pas 1500ms).
# ============================================================

import asyncio  # Pour gather() (ch.06)
import logging
import statistics
import time
from collections import Counter  # Pour le vote majoritaire sur les descriptions
from datetime import datetime, timezone

import httpx  # Client HTTP async partagé
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect

from app.metrics import APPELS_FOURNISSEUR, LATENCE_FOURNISSEUR, maj_circuit_breaker

from app import cache  # Module cache Redis (étape 5)
from app.circuit_breaker import circuit_breakers  # Registre des CB (étape 4)
from app.config import PROVIDER_TIMEOUT
from app.providers import open_meteo, openweather, weatherapi  # Les 3 providers
from app.schemas import (
    ComparaisonResponse,
    DonneesComparaison,
    DonneesMeteo,
    MeteoResponse,
    ResultatFournisseur,
)

logger = logging.getLogger(__name__)

# APIRouter = sous-routeur FastAPI (ch.05 exemple_04_apirouter_exception_handler)
# On le monte dans main.py avec app.include_router()
router = APIRouter(tags=["Météo"])


# ============================================================
# CLIENT HTTPX PARTAGÉ
# ============================================================
# On crée UN SEUL client httpx pour toute la durée de vie de l'app.
# Avantages : réutilisation des connexions TCP (connection pooling),
# partage des certificats TLS, meilleure performance globale.
# Le client est créé ici et injecté dans chaque provider.fetch().
_http_client: httpx.AsyncClient | None = None


async def get_http_client() -> httpx.AsyncClient:
    """
    Retourne le client httpx partagé, en le créant si nécessaire.
    Fonction de dépendance FastAPI (ch.05 exemple_03_dependencies).
    """
    global _http_client
    if _http_client is None or _http_client.is_closed:
        # Crée un client avec des limites de connexions raisonnables
        _http_client = httpx.AsyncClient(
            timeout=PROVIDER_TIMEOUT,     # Timeout global pour toutes les requêtes
            limits=httpx.Limits(
                max_connections=20,        # Max 20 connexions simultanées
                max_keepalive_connections=10,  # 10 connexions persistent entre requêtes
            ),
        )
    return _http_client


async def fermer_client_http() -> None:
    """Ferme proprement le client httpx partagé lors du shutdown de l'application."""
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()


# ============================================================
# SERVICE CACHE — abstraction injectable
# ============================================================

class ServiceCache:
    """Façade sur le module cache, injectable via Depends()."""

    def lire_cache_long(self, ville: str, pays: str):
        return cache.lire_cache_long(ville, pays)

    def ecrire_cache_long(self, reponse, ville: str, pays: str) -> None:
        cache.ecrire_cache_long(reponse, ville, pays)

    def lire_cache_court(self, fournisseur: str, ville: str, pays: str):
        return cache.lire_cache_court(fournisseur, ville, pays)

    def ecrire_cache_court(self, resultat, ville: str, pays: str) -> None:
        cache.ecrire_cache_court(resultat, ville, pays)

    def incrementer_compteur(self, ville: str, pays: str) -> None:
        cache.incrementer_compteur(ville, pays)

    def enregistrer_hit(self) -> None:
        cache.enregistrer_hit()

    def enregistrer_miss(self) -> None:
        cache.enregistrer_miss()

    def obtenir_stats(self) -> dict:
        return cache.obtenir_stats()


def get_service_cache() -> ServiceCache:
    """Dépendance FastAPI — retourne l'instance du service cache."""
    return ServiceCache()


# ============================================================
# FONCTIONS INTERNES D'ORCHESTRATION
# ============================================================

async def _appeler_provider(
    client: httpx.AsyncClient,
    provider_id: str,
    fetch_fn,
    ville: str,
    pays: str,
    cache_svc: ServiceCache,
) -> ResultatFournisseur | Exception:
    """
    Appelle UN fournisseur avec son circuit breaker.
    Retourne soit un ResultatFournisseur, soit l'exception capturée.
    On retourne l'exception au lieu de la lever pour que asyncio.gather
    puisse collecter les résultats et erreurs de tous les providers.

    Ordre des vérifications :
      1. Cache court → si présent, pas d'appel HTTP
      2. Circuit breaker → si OPEN, pas d'appel HTTP
      3. Appel HTTP réel → succès ou erreur enregistrée dans le CB
    """

    # ---- Étape 1 : vérifier le cache court ----
    en_cache = cache_svc.lire_cache_court(provider_id, ville, pays)
    if en_cache is not None:
        # Les données brutes de ce provider sont déjà en cache
        logger.debug("Cache court HIT pour %s / %s,%s", provider_id, ville, pays)
        return en_cache   # Retour immédiat, pas d'appel HTTP

    # ---- Étape 2 : vérifier le circuit breaker ----
    cb = circuit_breakers[provider_id]

    if not cb.peut_appeler():
        # Circuit OPEN : le provider est en panne, on ne l'appelle pas
        logger.warning(
            "Circuit OPEN pour %s — requête bloquée (%s,%s)",
            provider_id, ville, pays,
        )
        APPELS_FOURNISSEUR.labels(fournisseur=provider_id, statut="circuit_ouvert").inc()
        return RuntimeError(f"Circuit OPEN pour {provider_id}")

    # ---- Étape 3 : appel HTTP réel ----
    debut = time.monotonic()
    try:
        donnees: DonneesMeteo = await fetch_fn(client, ville, pays)

        # Succès → enregistrer dans le circuit breaker et les métriques
        cb.enregistrer_succes()
        maj_circuit_breaker(provider_id, cb.etat.value)
        APPELS_FOURNISSEUR.labels(fournisseur=provider_id, statut="succes").inc()
        LATENCE_FOURNISSEUR.labels(fournisseur=provider_id).observe(time.monotonic() - debut)

        # Construire le ResultatFournisseur
        resultat = ResultatFournisseur(
            fournisseur=provider_id,   # type: ignore
            donnees=donnees,
            depuis_cache=False,
        )

        # Écrire dans le cache court (TTL 5 min) pour les prochaines requêtes
        cache_svc.ecrire_cache_court(resultat, ville, pays)

        return resultat

    except Exception as e:
        # Échec → enregistrer l'erreur dans le circuit breaker et les métriques
        cb.enregistrer_erreur()
        maj_circuit_breaker(provider_id, cb.etat.value)
        APPELS_FOURNISSEUR.labels(fournisseur=provider_id, statut="erreur").inc()
        LATENCE_FOURNISSEUR.labels(fournisseur=provider_id).observe(time.monotonic() - debut)
        logger.error(
            "Erreur provider %s pour %s,%s : %s",
            provider_id, ville, pays, e,
        )
        return e   # On retourne l'exception (pas raise) pour gather()


def _fusionner_resultats(
    resultats: list[ResultatFournisseur | Exception],
    providers_ids: list[str],
) -> tuple[list[ResultatFournisseur], list[str]]:
    """
    Sépare les succès des échecs parmi les résultats de gather().

    Retourne :
        (succes, ids_ko) :
            succes  — liste des ResultatFournisseur valides
            ids_ko  — liste des identifiants des providers en erreur
    """
    succes: list[ResultatFournisseur] = []
    ids_ko: list[str] = []

    for provider_id, resultat in zip(providers_ids, resultats, strict=False):
        if isinstance(resultat, Exception):
            # Ce provider a échoué (erreur HTTP, timeout, circuit ouvert...)
            ids_ko.append(provider_id)
        else:
            # Ce provider a répondu avec succès
            succes.append(resultat)

    return succes, ids_ko


def _calculer_moyenne(valeurs: list[float]) -> float:
    """Calcule la moyenne d'une liste de flottants, arrondie à 1 décimale."""
    return round(sum(valeurs) / len(valeurs), 1)


def _calculer_indice_confiance(succes: list[ResultatFournisseur]) -> int:
    """
    Indice 0-100 mesurant l'accord entre fournisseurs sur la température.
    - 1 source : 50 (pas de comparaison possible)
    - Écart-type = 0°C → 100
    - Écart-type ≥ 5°C → 0
    """
    if len(succes) < 2:
        return 50
    temperatures = [r.donnees.temperature_c for r in succes]
    ecart = statistics.stdev(temperatures)
    return max(0, min(100, round(100 - ecart * 20)))


def _choisir_description(descriptions: list[str]) -> str:
    """
    Choisit la description météo par VOTE MAJORITAIRE.
    Si 2 providers disent "Ensoleillé" et 1 dit "Nuageux" → "Ensoleillé".
    En cas d'égalité, retourne le premier dans l'ordre alphabétique.
    """
    if not descriptions:
        return "Données insuffisantes"

    # Counter compte les occurrences de chaque description
    # most_common(1) retourne [(description_la_plus_fréquente, nb_votes)]
    compteur = Counter(descriptions)
    description_majoritaire, _ = compteur.most_common(1)[0]
    return description_majoritaire


def _construire_reponse(
    ville: str,
    pays: str,
    succes: list[ResultatFournisseur],
    ids_ko: list[str],
) -> MeteoResponse:
    """
    Fusionne les données de tous les providers ayant répondu.
    Calcule les moyennes et choisit la description par vote.
    """
    # Extraction des données de chaque provider réussi
    temperatures = [r.donnees.temperature_c for r in succes]
    humidites    = [r.donnees.humidite_pct  for r in succes]
    vents        = [r.donnees.vent_kmh      for r in succes]
    descriptions = [r.donnees.description   for r in succes]
    ids_ok       = [r.fournisseur           for r in succes]

    indice = _calculer_indice_confiance(succes)

    nb_ko = len(ids_ko)
    nb_ok = len(succes)
    avertissement = None

    if nb_ok == 1 and nb_ko >= 2:
        avertissement = (
            f"Un seul fournisseur a répondu sur {nb_ok + nb_ko}. "
            f"La ville « {ville} » ({pays}) est peut-être introuvable ou mal orthographiée — "
            f"les données retournées sont à considérer avec précaution."
        )
    elif nb_ok >= 2:
        ecart_temp = max(temperatures) - min(temperatures)
        if ecart_temp >= 8:
            avertissement = (
                f"Divergence importante entre fournisseurs ({ecart_temp:.1f}°C d'écart). "
                f"Vérifiez le code pays « {pays} » — il est peut-être incorrect pour {ville}."
            )
        elif indice < 40:
            avertissement = (
                f"Faible consensus inter-sources (indice {indice}%). "
                f"Les fournisseurs ne semblent pas interroger la même localisation."
            )

    return MeteoResponse(
        ville=ville,
        pays=pays,
        temperature_c=_calculer_moyenne(temperatures),
        humidite_pct=_calculer_moyenne(humidites),
        vent_kmh=_calculer_moyenne(vents),
        description=_choisir_description(descriptions),
        fournisseurs_ok=list(ids_ok),
        fournisseurs_ko=ids_ko,
        nb_sources=len(succes),
        depuis_cache=False,
        indice_confiance=indice,
        avertissement=avertissement,
    )


# ============================================================
# ROUTE PRINCIPALE : GET /meteo
# ============================================================

# Mapping provider_id → (module.fetch, circuit_breaker_key)
PROVIDERS = [
    ("openweather", openweather.fetch),
    ("open_meteo",  open_meteo.fetch),
    ("weatherapi",  weatherapi.fetch),
]


# ============================================================
# HELPER INTERNE — logique métier réutilisable (HTTP + WebSocket)
# ============================================================

async def _fetch_meteo(
    ville: str,
    pays: str,
    client: httpx.AsyncClient,
    cache_svc: ServiceCache,
) -> MeteoResponse:
    """
    Cœur de la logique /meteo, réutilisable sans injection FastAPI.
    Appelé par le route handler ET le WebSocket.
    """
    cache_svc.incrementer_compteur(ville, pays)

    en_cache_long = cache_svc.lire_cache_long(ville, pays)
    if en_cache_long is not None:
        logger.info("Cache long HIT pour %s,%s", ville, pays)
        cache_svc.enregistrer_hit()
        return en_cache_long

    cache_svc.enregistrer_miss()

    coroutines = [
        _appeler_provider(client, pid, fn, ville, pays, cache_svc)
        for pid, fn in PROVIDERS
    ]
    resultats: list = await asyncio.gather(*coroutines, return_exceptions=True)
    provider_ids = [pid for pid, _ in PROVIDERS]
    succes, ids_ko = _fusionner_resultats(resultats, provider_ids)

    logger.info(
        "Météo %s,%s — %d OK (%s), %d KO (%s)",
        ville, pays, len(succes), [r.fournisseur for r in succes], len(ids_ko), ids_ko,
    )

    if not succes:
        raise HTTPException(
            status_code=503,
            detail={
                "erreur": "Aucun fournisseur météo disponible",
                "fournisseurs_ko": ids_ko,
                "suggestion": "Réessayez dans quelques secondes.",
            },
        )

    reponse = _construire_reponse(ville, pays, succes, ids_ko)
    cache_svc.ecrire_cache_long(reponse, ville, pays)
    return reponse


# ============================================================
# ROUTE : GET /meteo
# ============================================================

@router.get(
    "/meteo",
    response_model=MeteoResponse,
    summary="Météo actuelle pour une ville",
    description=(
        "Interroge jusqu'à 3 fournisseurs en parallèle (OpenWeather, Open-Meteo, "
        "WeatherAPI), fusionne les résultats et les met en cache Redis. "
        "Objectif : réponse en moins de 800ms (p95)."
    ),
    responses={
        200: {"description": "Données météo fusionnées avec indice de confiance"},
        503: {"description": "Aucun fournisseur disponible"},
    },
)
async def get_meteo(
    ville: str = Query(..., min_length=1, max_length=100,
                       description="Nom de la ville", examples=["Paris"]),
    pays: str = Query(default="FR", min_length=2, max_length=2,
                      description="Code pays ISO 3166-1 alpha-2", examples=["FR"]),
    cache_svc: ServiceCache = Depends(get_service_cache),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> MeteoResponse:
    return await _fetch_meteo(ville.strip(), pays.strip().upper(), client, cache_svc)


# ============================================================
# ROUTE : GET /comparer
# ============================================================

@router.get(
    "/comparer",
    response_model=ComparaisonResponse,
    summary="Comparer les données brutes de chaque fournisseur",
    description=(
        "Interroge les 3 fournisseurs en parallèle et retourne leurs données brutes "
        "côte à côte, avec les écarts de mesure et un indice de consensus. "
        "Idéal pour comprendre pourquoi l'agrégateur a choisi certaines valeurs."
    ),
    tags=["Météo"],
)
async def comparer_sources(
    ville: str = Query(..., min_length=1, max_length=100, examples=["Paris"]),
    pays: str = Query(default="FR", min_length=2, max_length=2, examples=["FR"]),
    cache_svc: ServiceCache = Depends(get_service_cache),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> ComparaisonResponse:
    ville = ville.strip()
    pays = pays.strip().upper()

    coroutines = [
        _appeler_provider(client, pid, fn, ville, pays, cache_svc)
        for pid, fn in PROVIDERS
    ]
    resultats: list = await asyncio.gather(*coroutines, return_exceptions=True)
    provider_ids = [pid for pid, _ in PROVIDERS]
    succes, ids_ko = _fusionner_resultats(resultats, provider_ids)

    if not succes:
        raise HTTPException(status_code=503, detail="Aucun fournisseur disponible")

    sources = {
        r.fournisseur: DonneesComparaison(
            temperature_c=r.donnees.temperature_c,
            humidite_pct=r.donnees.humidite_pct,
            vent_kmh=r.donnees.vent_kmh,
            description=r.donnees.description,
            depuis_cache=r.depuis_cache,
        )
        for r in succes
    }

    ecarts: dict[str, float] = {}
    if len(succes) >= 2:
        ecarts = {
            "temperature_c": round(
                max(r.donnees.temperature_c for r in succes) -
                min(r.donnees.temperature_c for r in succes), 1
            ),
            "humidite_pct": round(
                max(r.donnees.humidite_pct for r in succes) -
                min(r.donnees.humidite_pct for r in succes), 1
            ),
            "vent_kmh": round(
                max(r.donnees.vent_kmh for r in succes) -
                min(r.donnees.vent_kmh for r in succes), 1
            ),
        }

    return ComparaisonResponse(
        ville=ville,
        pays=pays,
        sources=sources,
        ecarts=ecarts,
        indice_consensus=_calculer_indice_confiance(succes),
        fournisseurs_ok=[r.fournisseur for r in succes],
        fournisseurs_ko=ids_ko,
        genere_a=datetime.now(timezone.utc),
    )


# ============================================================
# WEBSOCKET : /ws/meteo/{ville}
# ============================================================

@router.websocket("/ws/meteo/{ville}")
async def ws_meteo(
    websocket: WebSocket,
    ville: str,
    pays: str = "FR",
    interval: int = 30,
) -> None:
    """
    Flux météo en temps réel via WebSocket.
    Envoie les données toutes les `interval` secondes (min 10, max 60).
    """
    await websocket.accept()
    logger.info("WebSocket ouvert — %s,%s (intervalle %ds)", ville, pays, interval)

    client = await get_http_client()
    cache_svc = ServiceCache()
    intervalle = max(10, min(60, interval))

    try:
        while True:
            try:
                reponse = await _fetch_meteo(
                    ville.strip(), pays.strip().upper(), client, cache_svc
                )
                await websocket.send_json(reponse.model_dump(mode="json"))
            except HTTPException as e:
                await websocket.send_json({"erreur": str(e.detail)})
            await asyncio.sleep(intervalle)
    except WebSocketDisconnect:
        logger.info("WebSocket fermé — %s,%s", ville, pays)
    except Exception as e:
        logger.error("Erreur WebSocket %s,%s : %s", ville, pays, e)