import asyncio
import logging
import statistics
import time
from collections import Counter
from datetime import datetime, timezone

import httpx
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)

from app.metrics import APPELS_FOURNISSEUR, LATENCE_FOURNISSEUR, maj_circuit_breaker

from app import cache
from app.config import SEUIL_CANICULE, SEUIL_GEL, SEUIL_VENT_FORT
from app.circuit_breaker import circuit_breakers
from app.config import PROVIDER_TIMEOUT
from app.providers import open_meteo, openweather, weatherapi
from app.schemas import (
    ComparaisonResponse,
    DonneesComparaison,
    DonneesMeteo,
    MeteoResponse,
    ResultatFournisseur,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Météo"])

_http_client: httpx.AsyncClient | None = None
_http_client_lock = asyncio.Lock()


async def get_http_client() -> httpx.AsyncClient:
    global _http_client
    async with _http_client_lock:
        if _http_client is None or _http_client.is_closed:
            _http_client = httpx.AsyncClient(
                timeout=PROVIDER_TIMEOUT,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
    return _http_client


async def fermer_client_http() -> None:
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()


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
    return ServiceCache()


async def _appeler_provider(
    client: httpx.AsyncClient,
    provider_id: str,
    fetch_fn,
    ville: str,
    pays: str,
    cache_svc: ServiceCache,
) -> ResultatFournisseur | Exception:
    """Appelle un fournisseur via son circuit breaker. Retourne l'exception au lieu de la lever."""
    en_cache = cache_svc.lire_cache_court(provider_id, ville, pays)
    if en_cache is not None:
        return en_cache

    cb = circuit_breakers[provider_id]
    if not cb.peut_appeler():
        logger.warning("Circuit OPEN pour %s (%s,%s)", provider_id, ville, pays)
        APPELS_FOURNISSEUR.labels(
            fournisseur=provider_id, statut="circuit_ouvert"
        ).inc()
        return RuntimeError(f"Circuit OPEN pour {provider_id}")

    debut = time.monotonic()
    try:
        donnees: DonneesMeteo = await fetch_fn(client, ville, pays)
        cb.enregistrer_succes()
        maj_circuit_breaker(provider_id, cb.etat.value)
        APPELS_FOURNISSEUR.labels(fournisseur=provider_id, statut="succes").inc()
        LATENCE_FOURNISSEUR.labels(fournisseur=provider_id).observe(
            time.monotonic() - debut
        )

        resultat = ResultatFournisseur(
            fournisseur=provider_id, donnees=donnees, depuis_cache=False
        )  # type: ignore
        cache_svc.ecrire_cache_court(resultat, ville, pays)
        return resultat

    except Exception as e:
        cb.enregistrer_erreur()
        maj_circuit_breaker(provider_id, cb.etat.value)
        APPELS_FOURNISSEUR.labels(fournisseur=provider_id, statut="erreur").inc()
        LATENCE_FOURNISSEUR.labels(fournisseur=provider_id).observe(
            time.monotonic() - debut
        )
        logger.error("Erreur provider %s pour %s,%s : %s", provider_id, ville, pays, e)
        return e


def _fusionner_resultats(
    resultats: list[ResultatFournisseur | Exception],
    providers_ids: list[str],
) -> tuple[list[ResultatFournisseur], list[str]]:
    succes: list[ResultatFournisseur] = []
    ids_ko: list[str] = []
    for provider_id, resultat in zip(providers_ids, resultats, strict=False):
        if isinstance(resultat, Exception):
            ids_ko.append(provider_id)
        else:
            succes.append(resultat)
    return succes, ids_ko


def _calculer_moyenne(valeurs: list[float]) -> float:
    return round(sum(valeurs) / len(valeurs), 1)


def _calculer_indice_confiance(succes: list[ResultatFournisseur]) -> int:
    """50 si 1 source, sinon 100 − écart_type_temp × 20 (borné 0–100)."""
    if len(succes) < 2:
        return 50
    temperatures = [r.donnees.temperature_c for r in succes]
    ecart = statistics.stdev(temperatures)
    return max(0, min(100, round(100 - ecart * 20)))


def _choisir_description(descriptions: list[str]) -> str:
    if not descriptions:
        return "Données insuffisantes"
    return Counter(descriptions).most_common(1)[0][0]


def _construire_reponse(
    ville: str,
    pays: str,
    succes: list[ResultatFournisseur],
    ids_ko: list[str],
) -> MeteoResponse:
    temperatures = [r.donnees.temperature_c for r in succes]
    humidites = [r.donnees.humidite_pct for r in succes]
    vents = [r.donnees.vent_kmh for r in succes]
    descriptions = [r.donnees.description for r in succes]
    ids_ok = [r.fournisseur for r in succes]

    indice = _calculer_indice_confiance(succes)
    nb_ko = len(ids_ko)
    nb_ok = len(succes)
    avertissement = None

    if nb_ok == 1 and nb_ko >= 2:
        avertissement = (
            f"Un seul fournisseur a répondu sur {nb_ok + nb_ko}. "
            f"La ville « {ville} » ({pays}) est peut-être introuvable ou mal orthographiée."
        )
    elif nb_ok >= 2:
        ecart_temp = max(temperatures) - min(temperatures)
        if ecart_temp >= 8:
            avertissement = (
                f"Divergence importante entre fournisseurs ({ecart_temp:.1f}°C d'écart). "
                f"Vérifiez le code pays « {pays} »."
            )
        elif indice < 40:
            avertissement = (
                f"Faible consensus inter-sources (indice {indice}%). "
                f"Les fournisseurs semblent interroger des localisations différentes."
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


PROVIDERS = [
    ("openweather", openweather.fetch),
    ("open_meteo", open_meteo.fetch),
    ("weatherapi", weatherapi.fetch),
]


async def _fetch_meteo(
    ville: str,
    pays: str,
    client: httpx.AsyncClient,
    cache_svc: ServiceCache,
) -> MeteoResponse:
    """Logique principale GET /meteo, réutilisée par le handler HTTP et le WebSocket."""
    cache_svc.incrementer_compteur(ville, pays)

    en_cache_long = cache_svc.lire_cache_long(ville, pays)
    if en_cache_long is not None:
        cache_svc.enregistrer_hit()
        cache.incrementer_score_ville(ville, pays)
        return en_cache_long

    cache_svc.enregistrer_miss()

    resultats: list = await asyncio.gather(
        *[
            _appeler_provider(client, pid, fn, ville, pays, cache_svc)
            for pid, fn in PROVIDERS
        ],
        return_exceptions=True,
    )

    provider_ids = [pid for pid, _ in PROVIDERS]
    succes, ids_ko = _fusionner_resultats(resultats, provider_ids)

    logger.info(
        "Météo %s,%s — %d OK (%s), %d KO (%s)",
        ville,
        pays,
        len(succes),
        [r.fournisseur for r in succes],
        len(ids_ko),
        ids_ko,
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
    _post_traiter_reponse(ville, pays, reponse)
    return reponse


def _verifier_alertes(ville: str, pays: str, reponse: MeteoResponse) -> None:
    temp = reponse.temperature_c
    vent = reponse.vent_kmh

    if temp >= SEUIL_CANICULE:
        cache.enregistrer_alerte(
            type_alerte="canicule",
            ville=ville,
            pays=pays,
            valeur=temp,
            seuil=SEUIL_CANICULE,
            message=f"Température élevée : {temp}°C à {ville} ({pays}).",
            niveau="danger" if temp >= SEUIL_CANICULE + 5 else "vigilance",
        )
    elif temp <= SEUIL_GEL:
        cache.enregistrer_alerte(
            type_alerte="gel",
            ville=ville,
            pays=pays,
            valeur=temp,
            seuil=SEUIL_GEL,
            message=f"Risque de gel : {temp}°C à {ville} ({pays}).",
            niveau="danger" if temp <= SEUIL_GEL - 5 else "vigilance",
        )

    if vent >= SEUIL_VENT_FORT:
        cache.enregistrer_alerte(
            type_alerte="vent_fort",
            ville=ville,
            pays=pays,
            valeur=vent,
            seuil=SEUIL_VENT_FORT,
            message=f"Vents forts : {vent} km/h à {ville} ({pays}).",
            niveau="danger" if vent >= SEUIL_VENT_FORT + 40 else "vigilance",
        )


def _post_traiter_reponse(ville: str, pays: str, reponse: MeteoResponse) -> None:
    cache.enregistrer_historique(
        ville=ville,
        pays=pays,
        temperature=reponse.temperature_c,
        description=reponse.description,
        humidite=reponse.humidite_pct,
        vent=reponse.vent_kmh,
    )
    cache.incrementer_score_ville(ville, pays)
    _verifier_alertes(ville, pays, reponse)


@router.get(
    "/meteo",
    response_model=MeteoResponse,
    summary="Météo actuelle pour une ville",
    description=(
        "Interroge jusqu'à 3 fournisseurs en parallèle (OpenWeather, Open-Meteo, WeatherAPI), "
        "fusionne les résultats et les met en cache Redis."
    ),
    responses={
        200: {"description": "Données météo fusionnées avec indice de confiance"},
        503: {"description": "Aucun fournisseur disponible"},
    },
)
async def get_meteo(
    ville: str = Query(
        ...,
        min_length=1,
        max_length=100,
        pattern=r"^[\w\s\-\'\.\,À-ɏ]+$",
        description="Nom de la ville",
        examples=["Paris"],
    ),
    pays: str = Query(
        default="FR",
        min_length=2,
        max_length=2,
        pattern=r"^[A-Za-z]{2}$",
        description="Code pays ISO 3166-1 alpha-2",
        examples=["FR"],
    ),
    cache_svc: ServiceCache = Depends(get_service_cache),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> MeteoResponse:
    return await _fetch_meteo(ville.strip(), pays.strip().upper(), client, cache_svc)


@router.get(
    "/comparer",
    response_model=ComparaisonResponse,
    summary="Comparer les données brutes de chaque fournisseur",
    description=(
        "Interroge les 3 fournisseurs en parallèle et retourne leurs données brutes "
        "côte à côte, avec les écarts de mesure et un indice de consensus."
    ),
    tags=["Météo"],
)
async def comparer_sources(
    ville: str = Query(
        ...,
        min_length=1,
        max_length=100,
        pattern=r"^[\w\s\-\'\.\,À-ɏ]+$",
        examples=["Paris"],
    ),
    pays: str = Query(
        default="FR",
        min_length=2,
        max_length=2,
        pattern=r"^[A-Za-z]{2}$",
        examples=["FR"],
    ),
    cache_svc: ServiceCache = Depends(get_service_cache),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> ComparaisonResponse:
    ville = ville.strip()
    pays = pays.strip().upper()

    resultats: list = await asyncio.gather(
        *[
            _appeler_provider(client, pid, fn, ville, pays, cache_svc)
            for pid, fn in PROVIDERS
        ],
        return_exceptions=True,
    )

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
                max(r.donnees.temperature_c for r in succes)
                - min(r.donnees.temperature_c for r in succes),
                1,
            ),
            "humidite_pct": round(
                max(r.donnees.humidite_pct for r in succes)
                - min(r.donnees.humidite_pct for r in succes),
                1,
            ),
            "vent_kmh": round(
                max(r.donnees.vent_kmh for r in succes)
                - min(r.donnees.vent_kmh for r in succes),
                1,
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


@router.websocket("/ws/meteo/{ville}")
async def ws_meteo(
    websocket: WebSocket,
    ville: str,
    pays: str = "FR",
    interval: int = 30,
) -> None:
    """Flux météo en temps réel — envoie les données toutes les `interval` secondes (10–60)."""
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
