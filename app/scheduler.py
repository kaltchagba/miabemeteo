# ============================================================
# app/scheduler.py — Pré-chauffe automatique du cache
# ============================================================
# Ce module planifie une tâche qui s'exécute toutes les 10 minutes
# pour pré-chauffer le cache Redis des 20 villes les plus consultées.
#
# Pourquoi pré-chauffer ?
#   Sans pré-chauffe, la première requête d'une ville après expiration
#   du cache (1h) doit attendre les appels HTTP aux 3 fournisseurs (~500ms).
#   Avec pré-chauffe, le cache est renouvelé AVANT expiration :
#   toutes les requêtes reçoivent une réponse en < 10ms depuis le cache.
#
# Librairie : APScheduler (BackgroundScheduler)
#   - Tourne dans un thread séparé (non-bloquant pour FastAPI)
#   - Se démarre au lancement de l'app (lifespan dans main.py)
#   - S'arrête proprement à la fermeture de l'app
# ============================================================

import asyncio                        # Pour exécuter les coroutines async depuis un thread
import logging
from datetime import datetime, timezone

import httpx
from apscheduler.schedulers.background import BackgroundScheduler   # Thread séparé
from apscheduler.triggers.interval import IntervalTrigger           # Déclencheur périodique

from app import cache                 # Pour lire les villes populaires et écrire le cache
from app.config import (
    TOP_CITIES_COUNT,                 # Nombre de villes à pré-chauffer (défaut: 20)
    SCHEDULER_INTERVAL_MINUTES,       # Intervalle en minutes (défaut: 10)
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

# ============================================================
# LISTE DES VILLES PAR DÉFAUT
# ============================================================
# Utilisée AU PREMIER DÉMARRAGE quand le compteur Redis est vide.
# Après quelques heures, les vraies villes populaires prennent le relais.
VILLES_DEFAUT: list[tuple[str, str]] = [
    ("Paris",          "FR"),
    ("Lomé",           "TG"),
    ("Lyon",           "FR"),
    ("Marseille",      "FR"),
    ("Dakar",          "SN"),
    ("Abidjan",        "CI"),
    ("Casablanca",     "MA"),
    ("Tunis",          "TN"),
    ("Alger",          "DZ"),
    ("Montréal",       "CA"),
    ("Bruxelles",      "BE"),
    ("Genève",         "CH"),
    ("New York",       "US"),
    ("Londres",        "GB"),
    ("Berlin",         "DE"),
    ("Madrid",         "ES"),
    ("Rome",           "IT"),
    ("Accra",          "GH"),
    ("Lagos",          "NG"),
    ("Nairobi",        "KE"),
]

# Mapping identifiant → module.fetch pour les appels aux providers
PROVIDERS_MAP = [
    ("openweather", openweather.fetch),
    ("open_meteo",  open_meteo.fetch),
    ("weatherapi",  weatherapi.fetch),
]


# ============================================================
# TÂCHE DE PRÉ-CHAUFFE (exécutée toutes les N minutes)
# ============================================================

async def _prechauffer_ville(
    client: httpx.AsyncClient,
    ville: str,
    pays: str,
) -> None:
    """
    Pré-chauffe le cache pour UNE ville :
    appelle les 3 providers en parallèle et écrit dans le cache long.

    Si le cache long est encore valide (< 1h), on ne fait rien
    pour éviter des appels API inutiles.
    """
    # Vérification : le cache long est-il encore valide ?
    en_cache = cache.lire_cache_long(ville, pays)
    if en_cache is not None:
        # Cache encore frais → pas besoin de pré-chauffer
        logger.debug("Pré-chauffe ignorée (cache valide) : %s,%s", ville, pays)
        return

    logger.info("Pré-chauffe en cours : %s,%s", ville, pays)

    # Appels parallèles aux 3 providers (même logique que router_meteo.py)
    cache_svc = ServiceCache()
    coroutines = [
        _appeler_provider(client, provider_id, fetch_fn, ville, pays, cache_svc)
        for provider_id, fetch_fn in PROVIDERS_MAP
    ]

    # gather() avec return_exceptions=True : une erreur n'arrête pas les autres
    resultats = await asyncio.gather(*coroutines, return_exceptions=True)

    # Séparation succès / échecs
    provider_ids = [pid for pid, _ in PROVIDERS_MAP]
    succes, ids_ko = _fusionner_resultats(resultats, provider_ids)

    if not succes:
        # Aucun provider n'a répondu → impossible de pré-chauffer
        logger.warning(
            "Pré-chauffe impossible pour %s,%s — tous les providers KO : %s",
            ville, pays, ids_ko,
        )
        return

    # Fusion et écriture dans le cache long
    reponse = _construire_reponse(ville, pays, succes, ids_ko)
    cache.ecrire_cache_long(reponse, ville, pays)

    logger.info(
        "Pré-chauffe OK : %s,%s — %.1f°C via %s",
        ville, pays, reponse.temperature_c, reponse.fournisseurs_ok,
    )


async def _tache_prechauffage() -> None:
    """
    Tâche principale exécutée à chaque déclenchement du scheduler.

    Algorithme :
    1. Lit les N villes les plus populaires depuis Redis
    2. Si Redis est vide (premier démarrage), utilise VILLES_DEFAUT
    3. Pré-chauffe chaque ville SÉQUENTIELLEMENT (pas en parallèle)
       Raison : éviter de surcharger les APIs externes avec 20 requêtes simultanées
    """
    debut = datetime.now(timezone.utc)
    logger.info("=== Début pré-chauffe scheduler (%s) ===", debut.strftime("%H:%M:%S"))

    # ---- Récupération des villes à pré-chauffer ----
    villes_populaires = cache.obtenir_villes_populaires(TOP_CITIES_COUNT)

    if villes_populaires:
        # Des villes ont déjà été consultées → on utilise les vraies données
        villes = [(v.title(), p.upper()) for v, p in villes_populaires]
        logger.info("Pré-chauffe des %d villes populaires", len(villes))
    else:
        # Premier démarrage ou Redis vide → liste par défaut
        villes = VILLES_DEFAUT[:TOP_CITIES_COUNT]
        logger.info("Pré-chauffe des %d villes par défaut", len(villes))

    # ---- Pré-chauffe de chaque ville ----
    nb_ok  = 0   # Compteur de succès
    nb_ko  = 0   # Compteur d'échecs

    # On crée UN seul client httpx pour toutes les villes de cette session
    async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
        for ville, pays in villes:
            try:
                await _prechauffer_ville(client, ville, pays)
                nb_ok += 1
            except Exception as e:
                # Une erreur sur une ville ne doit pas arrêter les autres
                nb_ko += 1
                logger.error("Erreur pré-chauffe %s,%s : %s", ville, pays, e)

    # ---- Résumé de la session ----
    duree = (datetime.now(timezone.utc) - debut).total_seconds()
    logger.info(
        "=== Fin pré-chauffe : %d OK, %d KO, durée %.1fs ===",
        nb_ok, nb_ko, duree,
    )


def _lancer_tache_sync() -> None:
    """
    Pont entre le monde synchrone (APScheduler) et le monde async (FastAPI).

    APScheduler appelle cette fonction dans un thread séparé.
    Cette fonction crée une nouvelle boucle asyncio pour exécuter
    la coroutine async _tache_prechauffage().

    Pourquoi une nouvelle boucle ?
    FastAPI tourne dans sa propre boucle asyncio (thread principal).
    On ne peut pas appeler la boucle de FastAPI depuis un autre thread
    sans risque de conditions de course. La solution propre est de
    créer une boucle asyncio dédiée au thread du scheduler.
    """
    # asyncio.run() crée une nouvelle boucle, exécute la coroutine, puis ferme la boucle
    asyncio.run(_tache_prechauffage())


# ============================================================
# CRÉATION ET GESTION DU SCHEDULER
# ============================================================

# Instance globale du scheduler (créée une fois, partagée par toute l'app)
_scheduler: BackgroundScheduler | None = None


def demarrer_scheduler() -> BackgroundScheduler:
    """
    Crée et démarre le scheduler APScheduler.
    Appelé dans le lifespan de FastAPI (startup).

    Retourne le scheduler pour pouvoir l'arrêter proprement au shutdown.
    """
    global _scheduler

    # Création du scheduler en mode "background" (thread séparé, non-bloquant)
    _scheduler = BackgroundScheduler(
        timezone="UTC",   # Toutes les heures en UTC pour éviter les problèmes de fuseau
    )

    # Ajout de la tâche avec déclenchement périodique
    _scheduler.add_job(
        func=_lancer_tache_sync,               # Fonction à appeler
        trigger=IntervalTrigger(               # Déclencheur : toutes les N minutes
            minutes=SCHEDULER_INTERVAL_MINUTES
        ),
        id="prechauffage_cache",               # Identifiant unique de la tâche
        name="Pré-chauffe cache météo",        # Nom lisible (pour les logs)
        replace_existing=True,                 # Si la tâche existe déjà, la remplacer
        max_instances=1,                       # Une seule instance simultanée max
                                               # (évite les chevauchements si la tâche
                                               #  dure plus longtemps que l'intervalle)
    )

    # Démarrage effectif du scheduler (lance le thread en arrière-plan)
    _scheduler.start()

    logger.info(
        "Scheduler démarré — pré-chauffe toutes les %d minutes pour %d villes",
        SCHEDULER_INTERVAL_MINUTES,
        TOP_CITIES_COUNT,
    )

    return _scheduler


def arreter_scheduler() -> None:
    """
    Arrête proprement le scheduler.
    Appelé dans le lifespan de FastAPI (shutdown).
    wait=True : attend que la tâche en cours se termine avant d'arrêter.
    """
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=True)   # Attend la fin de la tâche en cours
        logger.info("Scheduler arrêté proprement.")


def forcer_prechauffage() -> None:
    """
    Force l'exécution immédiate de la tâche de pré-chauffe.
    Utile pour tester ou pour déclencher manuellement via une route admin.
    """
    if _scheduler is None:
        logger.warning("Scheduler non démarré — impossible de forcer le pré-chauffe.")
        return

    _scheduler.run_job("prechauffage_cache")
    logger.info("Pré-chauffe forcé — exécution immédiate déclenchée.")