# ============================================================
# app/cache.py — Cache Redis à 2 niveaux
# ============================================================
# Ce module gère toute la logique de cache de l'application.
#
# NIVEAU 1 — Cache court (5 minutes, par fournisseur) :
#   Clé : "meteo:brut:{fournisseur}:{ville}:{pays}"
#   Contenu : données brutes d'UN fournisseur (ResultatFournisseur)
#   But : éviter de rappeler un fournisseur si on a les données fraîches
#
# NIVEAU 2 — Cache long (1 heure, consolidé) :
#   Clé : "meteo:consolidee:{ville}:{pays}"
#   Contenu : réponse fusionnée des 3 fournisseurs (MeteoResponse)
#   But : servir directement la réponse finale sans aucun calcul
#
# COMPTEURS — Suivi des villes populaires :
#   Clé : "meteo:compteur:{ville}:{pays}"
#   Contenu : nombre de fois que cette ville a été demandée
#   But : le scheduler sait quelles villes pré-chauffer (étape 7)
#
# MÉTRIQUES — Hit/miss pour le dashboard (étape 8) :
#   Clé : "meteo:stats:hits"   → nb de requêtes servies depuis le cache
#   Clé : "meteo:stats:misses" → nb de requêtes ayant nécessité des appels API
# ============================================================

import json  # Pour sérialiser/désérialiser les objets Python
import logging  # Pour journaliser les erreurs Redis sans crasher

import redis  # Client Redis (sync, suffisant pour nos besoins)

from app.metrics import OPERATIONS_CACHE

from app.config import (
    CACHE_COURT_TTL,  # 300 secondes = 5 minutes
    CACHE_LONG_TTL,  # 3600 secondes = 1 heure
    REDIS_URL,  # URL Redis depuis .env
)
from app.schemas import MeteoResponse, ResultatFournisseur  # Modèles Pydantic

# Logger dédié à ce module (apparaît comme "app.cache" dans les logs)
logger = logging.getLogger(__name__)


# ============================================================
# CONNEXION REDIS
# ============================================================
def creer_client_redis() -> redis.Redis:
    """
    Crée et retourne un client Redis configuré.

    On utilise decode_responses=True pour que Redis retourne
    des str Python au lieu de bytes bruts (plus pratique avec JSON).

    pool_pre_ping=True vérifie la connexion avant chaque commande
    et la rétablit si elle est tombée (bonne pratique ch.10).
    """
    return redis.from_url(
        REDIS_URL,
        decode_responses=True,   # str au lieu de bytes
        socket_connect_timeout=2,    # Timeout de connexion : 2 secondes
        socket_timeout=2,            # Timeout des commandes : 2 secondes
        retry_on_timeout=True,       # Réessaie automatiquement en cas de timeout
    )


# Instance Redis partagée dans tout le module (créée une seule fois)
# Si Redis est indisponible au démarrage, les opérations loggent et continuent.
try:
    _redis = creer_client_redis()
    _redis.ping()                    # Vérifie que Redis répond au démarrage
    logger.info("Connexion Redis établie avec succès : %s", REDIS_URL)
except Exception as e:
    logger.warning(
        "Redis indisponible au démarrage (%s). "
        "Le cache sera ignoré jusqu'au rétablissement.", e
    )
    _redis = None                    # type: ignore — sera vérifié avant chaque op


# ============================================================
# FONCTIONS UTILITAIRES INTERNES
# ============================================================

def _cle_court(fournisseur: str, ville: str, pays: str) -> str:
    """
    Génère la clé Redis du cache court (niveau fournisseur).
    Format : "meteo:brut:openweather:paris:fr"
    On met tout en minuscules pour éviter les doublons Paris/paris/PARIS.
    """
    return f"meteo:brut:{fournisseur}:{ville.lower()}:{pays.lower()}"


def _cle_long(ville: str, pays: str) -> str:
    """
    Génère la clé Redis du cache long (réponse consolidée).
    Format : "meteo:consolidee:paris:fr"
    """
    return f"meteo:consolidee:{ville.lower()}:{pays.lower()}"


def _cle_compteur(ville: str, pays: str) -> str:
    """
    Génère la clé Redis du compteur de popularité d'une ville.
    Format : "meteo:compteur:paris:fr"
    """
    return f"meteo:compteur:{ville.lower()}:{pays.lower()}"


def _redis_disponible() -> bool:
    """
    Vérifie si Redis est joignable. Retourne False si Redis est KO.
    Permet de dégrader gracieusement : si Redis est en panne,
    l'app continue de fonctionner sans cache (juste plus lentement).
    """
    global _redis
    if _redis is None:
        # Tentative de reconnexion
        try:
            _redis = creer_client_redis()
            _redis.ping()
            logger.info("Redis reconnecté avec succès.")
        except Exception:
            return False
    return True


# ============================================================
# CACHE COURT — Niveau fournisseur (TTL 5 minutes)
# ============================================================

def lire_cache_court(
    fournisseur: str,
    ville: str,
    pays: str,
) -> ResultatFournisseur | None:
    """
    Tente de lire les données brutes d'un fournisseur depuis le cache court.

    Retourne :
        ResultatFournisseur — si les données sont en cache et non expirées
        None                — si absent du cache ou Redis indisponible
    """
    if not _redis_disponible():
        return None   # Redis KO : on laisse passer l'appel HTTP réel

    cle = _cle_court(fournisseur, ville, pays)

    try:
        # redis.get() retourne None si la clé n'existe pas (ou est expirée)
        valeur_json = _redis.get(cle)

        if valeur_json is None:
            return None   # Cache miss : la donnée n'est pas en cache

        # Désérialisation JSON → dict Python → validation Pydantic
        donnees_dict = json.loads(valeur_json)
        resultat = ResultatFournisseur.model_validate(donnees_dict)

        # Marquer que ces données viennent du cache
        resultat.depuis_cache = True

        return resultat

    except Exception as e:
        # Erreur de désérialisation ou autre : on loggue et on retourne None
        # L'app continue en faisant un vrai appel HTTP
        logger.warning("Erreur lecture cache court [%s] : %s", cle, e)
        return None


def ecrire_cache_court(resultat: ResultatFournisseur, ville: str, pays: str) -> None:
    """
    Sauvegarde les données brutes d'un fournisseur dans le cache court.

    Paramètres :
        resultat — données d'un fournisseur à mettre en cache
        ville    — nom de la ville (pour construire la clé)
        pays     — code pays (pour construire la clé)
    """
    if not _redis_disponible():
        return   # Redis KO : on ignore silencieusement l'écriture en cache

    cle = _cle_court(resultat.fournisseur, ville, pays)

    try:
        # Sérialisation Pydantic → JSON string
        # model_dump() convertit l'objet en dict, puis json.dumps() en string
        valeur_json = json.dumps(
            resultat.model_dump(mode="json")   # mode="json" sérialise datetime en ISO 8601
        )

        # Écriture dans Redis avec expiration automatique (TTL)
        # ex=CACHE_COURT_TTL → Redis supprime la clé après 300 secondes
        _redis.set(cle, valeur_json, ex=CACHE_COURT_TTL)

    except Exception as e:
        logger.warning("Erreur écriture cache court [%s] : %s", cle, e)


# ============================================================
# CACHE LONG — Niveau consolidé (TTL 1 heure)
# ============================================================

def lire_cache_long(ville: str, pays: str) -> MeteoResponse | None:
    """
    Tente de lire la réponse consolidée depuis le cache long.

    Retourne :
        MeteoResponse — si la réponse complète est en cache
        None          — si absente du cache ou Redis indisponible
    """
    if not _redis_disponible():
        return None

    cle = _cle_long(ville, pays)

    try:
        valeur_json = _redis.get(cle)

        if valeur_json is None:
            return None   # Cache miss

        # Désérialisation et validation Pydantic
        donnees_dict = json.loads(valeur_json)
        reponse = MeteoResponse.model_validate(donnees_dict)

        # Marquer que cette réponse vient du cache long
        reponse.depuis_cache = True

        return reponse

    except Exception as e:
        logger.warning("Erreur lecture cache long [%s] : %s", cle, e)
        return None


def ecrire_cache_long(reponse: MeteoResponse, ville: str, pays: str) -> None:
    """
    Sauvegarde la réponse consolidée dans le cache long.

    Paramètres :
        reponse — réponse fusionnée des 3 fournisseurs
        ville   — nom de la ville
        pays    — code pays
    """
    if not _redis_disponible():
        return

    cle = _cle_long(ville, pays)

    try:
        valeur_json = json.dumps(reponse.model_dump(mode="json"))
        _redis.set(cle, valeur_json, ex=CACHE_LONG_TTL)   # Expire après 1 heure

    except Exception as e:
        logger.warning("Erreur écriture cache long [%s] : %s", cle, e)


def invalider_cache(ville: str, pays: str) -> None:
    """
    Supprime TOUTES les entrées de cache pour une ville donnée.
    Utile pour forcer un rafraîchissement (ex: données incorrectes signalées).
    Supprime le cache long ET les caches courts des 3 fournisseurs.
    """
    if not _redis_disponible():
        return

    # Liste de toutes les clés à supprimer pour cette ville
    cles_a_supprimer = [
        _cle_long(ville, pays),
        _cle_court("openweather", ville, pays),
        _cle_court("open_meteo", ville, pays),
        _cle_court("weatherapi", ville, pays),
    ]

    try:
        # redis.delete() accepte plusieurs clés d'un coup (plus efficace)
        _redis.delete(*cles_a_supprimer)
        logger.info("Cache invalidé pour %s, %s", ville, pays)

    except Exception as e:
        logger.warning("Erreur invalidation cache pour %s/%s : %s", ville, pays, e)


# ============================================================
# COMPTEURS DE POPULARITÉ — Pour le scheduler (étape 7)
# ============================================================

def incrementer_compteur(ville: str, pays: str) -> None:
    """
    Incrémente le compteur de requêtes pour une ville.
    Appelé à chaque requête GET /meteo pour suivre les villes populaires.
    Le compteur n'expire jamais (il s'accumule sur la durée de vie de Redis).
    """
    if not _redis_disponible():
        return

    cle = _cle_compteur(ville, pays)

    try:
        # INCR est atomique dans Redis : pas de problème de concurrence
        # Si la clé n'existe pas, Redis la crée avec la valeur 0 puis l'incrémente
        _redis.incr(cle)

    except Exception as e:
        logger.warning("Erreur incrémentation compteur [%s] : %s", cle, e)


def obtenir_villes_populaires(n: int) -> list[tuple[str, str]]:
    """
    Retourne les N villes les plus demandées, triées par popularité décroissante.
    Utilisé par le scheduler pour savoir quelles villes pré-chauffer.

    Retourne :
        Liste de tuples (ville, pays), ex: [("Paris", "FR"), ("Lomé", "TG"), ...]
    """
    if not _redis_disponible():
        return []

    try:
        # Recherche de toutes les clés de compteur
        # SCAN est préférable à KEYS en production (non-bloquant)
        cles = list(_redis.scan_iter("meteo:compteur:*"))

        if not cles:
            return []   # Aucune ville consultée encore

        # Récupération de tous les compteurs en une seule commande MGET
        # (plus efficace que N commandes GET séparées)
        valeurs = _redis.mget(cles)

        # Construction de la liste (clé, compteur)
        villes_compteurs: list[tuple[str, str, int]] = []
        for cle, valeur in zip(cles, valeurs, strict=False):
            if valeur is None:
                continue   # Clé expirée entre SCAN et MGET (rare mais possible)

            # Extraction ville et pays depuis la clé
            # Format : "meteo:compteur:paris:fr" → ["meteo", "compteur", "paris", "fr"]
            parties = cle.split(":")
            if len(parties) != 4:
                continue   # Format inattendu → on ignore

            _, _, ville, pays = parties          # Déstructuration
            villes_compteurs.append((ville, pays, int(valeur)))

        # Tri par compteur décroissant (les plus populaires en premier)
        villes_compteurs.sort(key=lambda x: x[2], reverse=True)

        # Retourne seulement les N premiers, sans le compteur
        return [(v, p) for v, p, _ in villes_compteurs[:n]]

    except Exception as e:
        logger.warning("Erreur lecture villes populaires : %s", e)
        return []


# ============================================================
# MÉTRIQUES — Hit/miss pour le dashboard (étape 8)
# ============================================================

def enregistrer_hit() -> None:
    """
    Incrémente le compteur de cache hits.
    Appelé quand une réponse est servie depuis le cache long.
    """
    OPERATIONS_CACHE.labels(niveau="l2", operation="hit").inc()
    if not _redis_disponible():
        return
    try:
        _redis.incr("meteo:stats:hits")
    except Exception:
        pass   # Les métriques ne sont pas critiques : on ignore les erreurs


def enregistrer_miss() -> None:
    """
    Incrémente le compteur de cache misses.
    Appelé quand une réponse nécessite des appels aux fournisseurs.
    """
    OPERATIONS_CACHE.labels(niveau="l2", operation="miss").inc()
    if not _redis_disponible():
        return
    try:
        _redis.incr("meteo:stats:misses")
    except Exception:
        pass


def obtenir_stats() -> dict[str, int]:
    """
    Retourne les statistiques globales de cache.
    Utilisé par GET /sante et le dashboard HTML (étape 8).

    Retourne :
        {"hits": 42, "misses": 8, "ratio_pct": 84}
    """
    if not _redis_disponible():
        return {"hits": 0, "misses": 0, "ratio_pct": 0}

    try:
        # Lecture des 2 compteurs en une seule commande
        hits_str   = _redis.get("meteo:stats:hits")
        misses_str = _redis.get("meteo:stats:misses")

        hits   = int(hits_str)   if hits_str   else 0
        misses = int(misses_str) if misses_str else 0
        total  = hits + misses

        # Calcul du ratio hit (pourcentage de requêtes servies depuis le cache)
        ratio = round((hits / total) * 100) if total > 0 else 0

        return {"hits": hits, "misses": misses, "ratio_pct": ratio}

    except Exception as e:
        logger.warning("Erreur lecture stats cache : %s", e)
        return {"hits": 0, "misses": 0, "ratio_pct": 0}