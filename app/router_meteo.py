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
from collections import Counter  # Pour le vote majoritaire sur les descriptions

import httpx  # Client HTTP async partagé
from fastapi import APIRouter, HTTPException, Query  # FastAPI (ch.05)

from app import cache  # Module cache Redis (étape 5)
from app.circuit_breaker import circuit_breakers  # Registre des CB (étape 4)
from app.config import PROVIDER_TIMEOUT
from app.providers import open_meteo, openweather, weatherapi  # Les 3 providers
from app.schemas import (
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


# ============================================================
# FONCTIONS INTERNES D'ORCHESTRATION
# ============================================================

async def _appeler_provider(
    client: httpx.AsyncClient,
    provider_id: str,
    fetch_fn,
    ville: str,
    pays: str,
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
    en_cache = cache.lire_cache_court(provider_id, ville, pays)
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
        return RuntimeError(f"Circuit OPEN pour {provider_id}")

    # ---- Étape 3 : appel HTTP réel ----
    try:
        donnees: DonneesMeteo = await fetch_fn(client, ville, pays)

        # Succès → enregistrer dans le circuit breaker
        cb.enregistrer_succes()

        # Construire le ResultatFournisseur
        resultat = ResultatFournisseur(
            fournisseur=provider_id,   # type: ignore
            donnees=donnees,
            depuis_cache=False,
        )

        # Écrire dans le cache court (TTL 5 min) pour les prochaines requêtes
        cache.ecrire_cache_court(resultat, ville, pays)

        return resultat

    except Exception as e:
        # Échec → enregistrer l'erreur dans le circuit breaker
        # (peut ouvrir le circuit si seuil atteint)
        cb.enregistrer_erreur()
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
        200: {"description": "Données météo fusionnées"},
        503: {"description": "Aucun fournisseur disponible"},
    },
)
async def get_meteo(
    ville: str = Query(
        ...,                          # Obligatoire
        min_length=1,
        max_length=100,
        description="Nom de la ville (ex: Paris, Lomé)",
        examples=["Paris"],
    ),
    pays: str = Query(
        default="FR",
        min_length=2,
        max_length=2,
        description="Code pays ISO 3166-1 alpha-2 (ex: FR, TG, US)",
        examples=["FR"],
    ),
) -> MeteoResponse:
    """
    Endpoint principal de l'agrégateur météo.

    Flux d'exécution :
    1. Normaliser les paramètres (minuscules → majuscules)
    2. Incrémenter le compteur de popularité (pour le scheduler)
    3. Vérifier le cache long → répondre immédiatement si hit
    4. Appeler les 3 providers EN PARALLÈLE avec asyncio.gather()
    5. Filtrer les succès / échecs
    6. Lever 503 si AUCUN provider n'a répondu
    7. Fusionner les résultats et écrire dans le cache long
    8. Retourner la réponse
    """

    # ---- Étape 1 : Normalisation des paramètres ----
    ville = ville.strip()        # Supprime les espaces autour du nom
    pays  = pays.strip().upper() # Toujours en majuscules (ex: "fr" → "FR")

    # ---- Étape 2 : Compteur de popularité ----
    # Permet au scheduler de savoir quelles villes pré-chauffer (étape 7)
    cache.incrementer_compteur(ville, pays)

    # ---- Étape 3 : Vérification du cache long ----
    # Si la réponse consolidée est en cache (< 1h), on la retourne directement
    en_cache_long = cache.lire_cache_long(ville, pays)
    if en_cache_long is not None:
        logger.info("Cache long HIT pour %s,%s", ville, pays)
        cache.enregistrer_hit()          # Métriques dashboard
        return en_cache_long             # Réponse en quelques millisecondes

    # Cache miss → on va chercher les données fraîches
    cache.enregistrer_miss()

    # ---- Étape 4 : Appels parallèles aux 3 providers ----
    # On crée la liste des coroutines SANS les exécuter encore
    client = await get_http_client()

    coroutines = [
        _appeler_provider(client, provider_id, fetch_fn, ville, pays)
        for provider_id, fetch_fn in PROVIDERS
    ]

    # asyncio.gather() exécute TOUTES les coroutines simultanément.
    # return_exceptions=True : une exception n'annule pas les autres.
    # Les exceptions sont retournées dans la liste au lieu d'être levées.
    # Ex : [ResultatFournisseur, TimeoutError, ResultatFournisseur]
    resultats: list = await asyncio.gather(*coroutines, return_exceptions=True)

    # ---- Étape 5 : Séparation succès / échecs ----
    provider_ids = [pid for pid, _ in PROVIDERS]
    succes, ids_ko = _fusionner_resultats(resultats, provider_ids)

    logger.info(
        "Météo %s,%s — %d OK (%s), %d KO (%s)",
        ville, pays,
        len(succes), [r.fournisseur for r in succes],
        len(ids_ko), ids_ko,
    )

    # ---- Étape 6 : Vérification qu'au moins 1 provider a répondu ----
    if not succes:
        # Tous les providers ont échoué → service indisponible
        raise HTTPException(
            status_code=503,
            detail={
                "erreur": "Aucun fournisseur météo disponible",
                "fournisseurs_ko": ids_ko,
                "suggestion": "Réessayez dans quelques secondes.",
            },
        )

    # ---- Étape 7 : Fusion et mise en cache long ----
    reponse = _construire_reponse(ville, pays, succes, ids_ko)

    # Écriture dans le cache long (TTL 1 heure)
    # Les prochaines requêtes pour cette ville seront servies depuis le cache
    cache.ecrire_cache_long(reponse, ville, pays)

    # ---- Étape 8 : Retour de la réponse ----
    return reponse