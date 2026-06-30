import json
import logging

import redis

from app.metrics import OPERATIONS_CACHE

from app.config import (
    CACHE_COURT_TTL,
    CACHE_LONG_TTL,
    REDIS_URL,
    HISTORIQUE_MAX_ENTREES,
    TTL_ALERTE,
)
from app.schemas import MeteoResponse, ResultatFournisseur

logger = logging.getLogger(__name__)


def creer_client_redis() -> redis.Redis:
    return redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
        retry_on_timeout=True,
    )


try:
    _redis = creer_client_redis()
    _redis.ping()
    logger.info("Connexion Redis établie : %s", REDIS_URL)
except Exception as e:
    logger.warning("Redis indisponible au démarrage (%s). Cache ignoré jusqu'au rétablissement.", e)
    _redis = None  # type: ignore


def _cle_court(fournisseur: str, ville: str, pays: str) -> str:
    # Format : meteo:brut:{fournisseur}:{ville}:{pays} — tout en minuscules
    return f"meteo:brut:{fournisseur}:{ville.lower()}:{pays.lower()}"


def _cle_long(ville: str, pays: str) -> str:
    return f"meteo:consolidee:{ville.lower()}:{pays.lower()}"


def _cle_compteur(ville: str, pays: str) -> str:
    return f"meteo:compteur:{ville.lower()}:{pays.lower()}"


def _redis_disponible() -> bool:
    """Tente une reconnexion si Redis est KO. Retourne False si indisponible (fail-open)."""
    global _redis
    if _redis is None:
        try:
            _redis = creer_client_redis()
            _redis.ping()
            logger.info("Redis reconnecté.")
        except Exception:
            return False
    return True


def lire_cache_court(fournisseur: str, ville: str, pays: str) -> ResultatFournisseur | None:
    """Retourne les données brutes d'un fournisseur depuis le cache L1, ou None."""
    if not _redis_disponible():
        return None
    cle = _cle_court(fournisseur, ville, pays)
    try:
        valeur_json = _redis.get(cle)
        if valeur_json is None:
            return None
        resultat = ResultatFournisseur.model_validate(json.loads(valeur_json))
        resultat.depuis_cache = True
        return resultat
    except Exception as e:
        logger.warning("Erreur lecture cache court [%s] : %s", cle, e)
        return None


def ecrire_cache_court(resultat: ResultatFournisseur, ville: str, pays: str) -> None:
    if not _redis_disponible():
        return
    cle = _cle_court(resultat.fournisseur, ville, pays)
    try:
        _redis.set(cle, json.dumps(resultat.model_dump(mode="json")), ex=CACHE_COURT_TTL)
    except Exception as e:
        logger.warning("Erreur écriture cache court [%s] : %s", cle, e)


def lire_cache_long(ville: str, pays: str) -> MeteoResponse | None:
    """Retourne la réponse consolidée depuis le cache L2 (1h), ou None."""
    if not _redis_disponible():
        return None
    cle = _cle_long(ville, pays)
    try:
        valeur_json = _redis.get(cle)
        if valeur_json is None:
            return None
        reponse = MeteoResponse.model_validate(json.loads(valeur_json))
        reponse.depuis_cache = True
        return reponse
    except Exception as e:
        logger.warning("Erreur lecture cache long [%s] : %s", cle, e)
        return None


def ecrire_cache_long(reponse: MeteoResponse, ville: str, pays: str) -> None:
    if not _redis_disponible():
        return
    cle = _cle_long(ville, pays)
    try:
        _redis.set(cle, json.dumps(reponse.model_dump(mode="json")), ex=CACHE_LONG_TTL)
    except Exception as e:
        logger.warning("Erreur écriture cache long [%s] : %s", cle, e)


def invalider_cache(ville: str, pays: str) -> None:
    """Supprime le cache L2 et les 3 caches L1 pour une ville."""
    if not _redis_disponible():
        return
    cles = [
        _cle_long(ville, pays),
        _cle_court("openweather", ville, pays),
        _cle_court("open_meteo", ville, pays),
        _cle_court("weatherapi", ville, pays),
    ]
    try:
        _redis.delete(*cles)
        logger.info("Cache invalidé pour %s, %s", ville, pays)
    except Exception as e:
        logger.warning("Erreur invalidation cache %s/%s : %s", ville, pays, e)


def incrementer_compteur(ville: str, pays: str) -> None:
    if not _redis_disponible():
        return
    try:
        _redis.incr(_cle_compteur(ville, pays))
    except Exception as e:
        logger.warning("Erreur compteur [%s] : %s", _cle_compteur(ville, pays), e)


def obtenir_villes_populaires(n: int) -> list[tuple[str, str]]:
    if not _redis_disponible():
        return []
    try:
        # SCAN non-bloquant (préférable à KEYS en production)
        cles = list(_redis.scan_iter("meteo:compteur:*"))
        if not cles:
            return []
        valeurs = _redis.mget(cles)
        villes_compteurs: list[tuple[str, str, int]] = []
        for cle, valeur in zip(cles, valeurs, strict=False):
            if valeur is None:
                continue
            parties = cle.split(":")
            if len(parties) != 4:
                continue
            _, _, ville, pays = parties
            villes_compteurs.append((ville, pays, int(valeur)))
        villes_compteurs.sort(key=lambda x: x[2], reverse=True)
        return [(v, p) for v, p, _ in villes_compteurs[:n]]
    except Exception as e:
        logger.warning("Erreur villes populaires : %s", e)
        return []


def enregistrer_hit() -> None:
    OPERATIONS_CACHE.labels(niveau="l2", operation="hit").inc()
    if not _redis_disponible():
        return
    try:
        _redis.incr("meteo:stats:hits")
    except Exception:
        pass


def enregistrer_miss() -> None:
    OPERATIONS_CACHE.labels(niveau="l2", operation="miss").inc()
    if not _redis_disponible():
        return
    try:
        _redis.incr("meteo:stats:misses")
    except Exception:
        pass


def _cle_historique(ville: str, pays: str) -> str:
    return f"meteo:historique:{ville.lower()}:{pays.lower()}"


def enregistrer_historique(
    ville: str,
    pays: str,
    temperature: float,
    description: str,
    humidite: float,
    vent: float,
) -> None:
    """Ajoute une mesure dans l'historique (RPUSH + LTRIM pour fenêtre glissante)."""
    if not _redis_disponible():
        return
    cle = _cle_historique(ville, pays)
    try:
        from datetime import datetime, timezone
        entree = json.dumps({
            "timestamp":    datetime.now(timezone.utc).isoformat(),
            "temperature_c": temperature,
            "description":  description,
            "humidite_pct": humidite,
            "vent_kmh":     vent,
        })
        _redis.rpush(cle, entree)
        _redis.ltrim(cle, -HISTORIQUE_MAX_ENTREES, -1)
    except Exception as e:
        logger.warning("Erreur historique [%s] : %s", cle, e)


def lire_historique(ville: str, pays: str, n: int = 48) -> list[dict]:
    if not _redis_disponible():
        return []
    cle = _cle_historique(ville, pays)
    try:
        return [json.loads(e) for e in _redis.lrange(cle, -n, -1) if e]
    except Exception as e:
        logger.warning("Erreur lecture historique [%s] : %s", cle, e)
        return []


_CLE_TOP_SCORES = "meteo:top:scores"


def incrementer_score_ville(ville: str, pays: str) -> None:
    if not _redis_disponible():
        return
    membre = f"{ville.lower()}:{pays.lower()}"
    try:
        _redis.zincrby(_CLE_TOP_SCORES, 1, membre)
    except Exception as e:
        logger.warning("Erreur score ville [%s] : %s", membre, e)


def obtenir_top_villes_score(n: int = 10) -> list[dict]:
    if not _redis_disponible():
        return []
    try:
        resultats = _redis.zrevrange(_CLE_TOP_SCORES, 0, n - 1, withscores=True)
        villes = []
        for rang, (membre, score) in enumerate(resultats, 1):
            parties = membre.split(":", 1)
            if len(parties) == 2:
                villes.append({
                    "rang":        rang,
                    "ville":       parties[0].capitalize(),
                    "pays":        parties[1].upper(),
                    "nb_requetes": int(score),
                })
        return villes
    except Exception as e:
        logger.warning("Erreur top villes : %s", e)
        return []


def obtenir_total_requetes() -> int:
    if not _redis_disponible():
        return 0
    try:
        membres = _redis.zrange(_CLE_TOP_SCORES, 0, -1, withscores=True)
        return int(sum(score for _, score in membres))
    except Exception:
        return 0


def _cle_alerte(type_alerte: str, ville: str, pays: str) -> str:
    return f"meteo:alerte:{type_alerte}:{ville.lower()}:{pays.lower()}"


def enregistrer_alerte(
    type_alerte: str,
    ville: str,
    pays: str,
    valeur: float,
    seuil: float,
    message: str,
    niveau: str = "vigilance",
) -> None:
    """Enregistre une alerte dans Redis avec TTL automatique (1h par défaut)."""
    if not _redis_disponible():
        return
    cle = _cle_alerte(type_alerte, ville, pays)
    try:
        from datetime import datetime, timezone
        alerte = json.dumps({
            "type_alerte":  type_alerte,
            "niveau":       niveau,
            "ville":        ville,
            "pays":         pays,
            "valeur":       valeur,
            "seuil":        seuil,
            "message":      message,
            "declenchee_a": datetime.now(timezone.utc).isoformat(),
        })
        _redis.set(cle, alerte, ex=TTL_ALERTE)
    except Exception as e:
        logger.warning("Erreur alerte [%s] : %s", cle, e)


def lire_alertes_actives() -> list[dict]:
    """Retourne toutes les alertes non expirées. SCAN utilisé à la place de KEYS."""
    if not _redis_disponible():
        return []
    try:
        cles = list(_redis.scan_iter("meteo:alerte:*"))
        if not cles:
            return []
        alertes = []
        for valeur in _redis.mget(cles):
            if valeur:
                try:
                    alertes.append(json.loads(valeur))
                except json.JSONDecodeError:
                    pass
        alertes.sort(key=lambda a: a.get("declenchee_a", ""), reverse=True)
        return alertes
    except Exception as e:
        logger.warning("Erreur lecture alertes : %s", e)
        return []


def verifier_rate_limit(ip: str, limite: int, fenetre: int) -> tuple[bool, int]:
    """INCR atomique + EXPIRE sur la première requête. Fail-open si Redis KO."""
    if not _redis_disponible():
        return True, 0
    cle = f"rl:{ip}"
    try:
        nb = _redis.incr(cle)
        if nb == 1:
            _redis.expire(cle, fenetre)
        return nb <= limite, nb
    except Exception:
        return True, 0


def obtenir_stats() -> dict[str, int]:
    if not _redis_disponible():
        return {"hits": 0, "misses": 0, "ratio_pct": 0}
    try:
        hits_str   = _redis.get("meteo:stats:hits")
        misses_str = _redis.get("meteo:stats:misses")
        hits   = int(hits_str)   if hits_str   else 0
        misses = int(misses_str) if misses_str else 0
        total  = hits + misses
        ratio  = round((hits / total) * 100) if total > 0 else 0
        return {"hits": hits, "misses": misses, "ratio_pct": ratio}
    except Exception as e:
        logger.warning("Erreur stats cache : %s", e)
        return {"hits": 0, "misses": 0, "ratio_pct": 0}
