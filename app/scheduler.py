import asyncio
import logging
from datetime import datetime, timezone

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app import cache
from app.config import (
    TOP_CITIES_COUNT,
    SCHEDULER_INTERVAL_MINUTES,
    PROVIDER_TIMEOUT,
)
from app.providers import openweather, open_meteo, weatherapi
from app.router_meteo import (
    ServiceCache,
    _appeler_provider,
    _fusionner_resultats,
    _construire_reponse,
)

logger = logging.getLogger(__name__)

# Villes utilisées au premier démarrage (avant que Redis ait des données de popularité)
VILLES_DEFAUT: list[tuple[str, str]] = [
    ("Lomé",        "TG"),
    ("Accra",       "GH"),
    ("Abidjan",     "CI"),
    ("Cotonou",     "BJ"),
    ("Lagos",       "NG"),
    ("Dakar",       "SN"),
    ("Bamako",      "ML"),
    ("Ouagadougou", "BF"),
    ("Niamey",      "NE"),
    ("Conakry",     "GN"),
    ("Freetown",    "SL"),
    ("Yaoundé",     "CM"),
    ("Kinshasa",    "CD"),
    ("Nairobi",     "KE"),
    ("Addis-Abeba", "ET"),
    ("Paris",       "FR"),
    ("New York",    "US"),
    ("Dubai",       "AE"),
    ("Londres",     "GB"),
    ("Le Caire",    "EG"),
]

PROVIDERS_MAP = [
    ("openweather", openweather.fetch),
    ("open_meteo",  open_meteo.fetch),
    ("weatherapi",  weatherapi.fetch),
]


async def _prechauffer_ville(client: httpx.AsyncClient, ville: str, pays: str) -> None:
    """Pré-chauffe le cache L2 pour une ville si le cache est expiré."""
    if cache.lire_cache_long(ville, pays) is not None:
        logger.debug("Pré-chauffe ignorée (cache valide) : %s,%s", ville, pays)
        return

    logger.info("Pré-chauffe en cours : %s,%s", ville, pays)

    cache_svc = ServiceCache()
    resultats = await asyncio.gather(*[
        _appeler_provider(client, pid, fn, ville, pays, cache_svc)
        for pid, fn in PROVIDERS_MAP
    ], return_exceptions=True)

    succes, ids_ko = _fusionner_resultats(resultats, [pid for pid, _ in PROVIDERS_MAP])

    if not succes:
        logger.warning("Pré-chauffe impossible pour %s,%s — tous KO : %s", ville, pays, ids_ko)
        return

    reponse = _construire_reponse(ville, pays, succes, ids_ko)
    cache.ecrire_cache_long(reponse, ville, pays)
    logger.info("Pré-chauffe OK : %s,%s — %.1f°C", ville, pays, reponse.temperature_c)


async def _tache_prechauffage() -> None:
    """Pré-chauffe les villes populaires (ou les villes par défaut au premier démarrage).
    Traitement séquentiel pour ne pas saturer les quotas des APIs externes."""
    debut = datetime.now(timezone.utc)
    logger.info("Début pré-chauffe scheduler (%s)", debut.strftime("%H:%M:%S"))

    villes_populaires = cache.obtenir_villes_populaires(TOP_CITIES_COUNT)
    if villes_populaires:
        villes = [(v.title(), p.upper()) for v, p in villes_populaires]
    else:
        villes = VILLES_DEFAUT[:TOP_CITIES_COUNT]

    nb_ok = nb_ko = 0
    async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
        for ville, pays in villes:
            try:
                await _prechauffer_ville(client, ville, pays)
                nb_ok += 1
            except Exception as e:
                nb_ko += 1
                logger.error("Erreur pré-chauffe %s,%s : %s", ville, pays, e)

    duree = (datetime.now(timezone.utc) - debut).total_seconds()
    logger.info("Fin pré-chauffe : %d OK, %d KO, %.1fs", nb_ok, nb_ko, duree)


def _lancer_tache_sync() -> None:
    """Pont sync→async : APScheduler tourne dans un thread, asyncio.run() crée sa propre boucle."""
    asyncio.run(_tache_prechauffage())


_scheduler: BackgroundScheduler | None = None


def demarrer_scheduler() -> BackgroundScheduler:
    global _scheduler
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        func=_lancer_tache_sync,
        trigger=IntervalTrigger(minutes=SCHEDULER_INTERVAL_MINUTES),
        id="prechauffage_cache",
        name="Pré-chauffe cache météo",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.start()
    logger.info(
        "Scheduler démarré — pré-chauffe toutes les %d min pour %d villes",
        SCHEDULER_INTERVAL_MINUTES, TOP_CITIES_COUNT,
    )
    return _scheduler


def arreter_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=True)
        logger.info("Scheduler arrêté.")


def forcer_prechauffage() -> None:
    if _scheduler is None:
        logger.warning("Scheduler non démarré.")
        return
    _scheduler.run_job("prechauffage_cache")
