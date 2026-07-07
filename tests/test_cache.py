import fakeredis
import pytest

import app.cache as cache_module
from app.config import CACHE_COURT_TTL, CACHE_LONG_TTL
from app.schemas import DonneesMeteo, MeteoResponse, ResultatFournisseur


def _resultat(fournisseur: str = "openweather") -> ResultatFournisseur:
    return ResultatFournisseur(
        fournisseur=fournisseur,  # type: ignore[arg-type]
        donnees=DonneesMeteo(
            temperature_c=22.0,
            humidite_pct=60.0,
            vent_kmh=15.0,
            description="Ensoleillé",
            code_meteo=800,
        ),
        depuis_cache=False,
    )


def _reponse() -> MeteoResponse:
    return MeteoResponse(
        ville="Paris",
        pays="FR",
        temperature_c=22.0,
        humidite_pct=60.0,
        vent_kmh=15.0,
        description="Ensoleillé",
        fournisseurs_ok=["openweather"],
        fournisseurs_ko=[],
        nb_sources=1,
        depuis_cache=False,
    )


@pytest.fixture
def redis_fake():
    """Substitue le client Redis par un Redis en mémoire (fakeredis)."""
    r = fakeredis.FakeRedis(decode_responses=True)
    ancienne_valeur = cache_module._redis
    cache_module._redis = r
    yield r
    cache_module._redis = ancienne_valeur


@pytest.fixture
def redis_ko(monkeypatch):
    """Simule Redis totalement indisponible (None + reconnexion impossible)."""
    monkeypatch.setattr(cache_module, "_redis", None)

    def _connexion_impossible():
        raise ConnectionError("Redis indisponible")

    monkeypatch.setattr(cache_module, "creer_client_redis", _connexion_impossible)


def test_lire_cache_court_miss(redis_fake):
    """Cache vide → retourne None sans lever d'exception."""
    assert cache_module.lire_cache_court("openweather", "Paris", "FR") is None


def test_ecrire_puis_lire_cache_court(redis_fake):
    """Résultat écrit → relisible et marqué depuis_cache=True."""
    cache_module.ecrire_cache_court(_resultat("openweather"), "Paris", "FR")
    lu = cache_module.lire_cache_court("openweather", "Paris", "FR")

    assert lu is not None
    assert lu.fournisseur == "openweather"
    assert lu.donnees.temperature_c == 22.0
    assert lu.depuis_cache is True


def test_ecrire_cache_court_ttl(redis_fake):
    """TTL de la clé Redis correspond à CACHE_COURT_TTL."""
    cache_module.ecrire_cache_court(_resultat("openweather"), "Lyon", "FR")
    ttl = redis_fake.ttl("meteo:brut:openweather:lyon:fr")
    assert 0 < ttl <= CACHE_COURT_TTL


def test_cache_court_cle_normalisee(redis_fake):
    """Ville et pays sont mis en minuscules dans la clé Redis."""
    cache_module.ecrire_cache_court(_resultat("weatherapi"), "PARIS", "FR")
    assert redis_fake.exists("meteo:brut:weatherapi:paris:fr") == 1


def test_lire_cache_court_redis_ko(redis_ko):
    """Redis indisponible → retourne None sans planter."""
    assert cache_module.lire_cache_court("openweather", "Paris", "FR") is None


def test_ecrire_cache_court_redis_ko(redis_ko):
    """Redis indisponible → écriture silencieusement ignorée."""
    cache_module.ecrire_cache_court(_resultat(), "Paris", "FR")


def test_lire_cache_long_miss(redis_fake):
    """Cache vide → retourne None."""
    assert cache_module.lire_cache_long("Paris", "FR") is None


def test_ecrire_puis_lire_cache_long(redis_fake):
    """Réponse consolidée écrite → relisible et marquée depuis_cache=True."""
    cache_module.ecrire_cache_long(_reponse(), "Paris", "FR")
    lue = cache_module.lire_cache_long("Paris", "FR")

    assert lue is not None
    assert lue.ville == "Paris"
    assert lue.temperature_c == 22.0
    assert lue.depuis_cache is True


def test_ecrire_cache_long_ttl(redis_fake):
    """TTL de la clé consolidée correspond à CACHE_LONG_TTL."""
    cache_module.ecrire_cache_long(_reponse(), "Lomé", "TG")
    ttl = redis_fake.ttl("meteo:consolidee:lomé:tg")
    assert 0 < ttl <= CACHE_LONG_TTL


def test_lire_cache_long_redis_ko(redis_ko):
    """Redis indisponible → retourne None."""
    assert cache_module.lire_cache_long("Paris", "FR") is None


def test_ecrire_cache_long_redis_ko(redis_ko):
    """Redis indisponible → écriture ignorée sans exception."""
    cache_module.ecrire_cache_long(_reponse(), "Paris", "FR")


def test_invalider_cache_supprime_toutes_les_cles(redis_fake):
    """invalider_cache() efface le cache long et les 3 caches courts."""
    cache_module.ecrire_cache_long(_reponse(), "Paris", "FR")
    cache_module.ecrire_cache_court(_resultat("openweather"), "Paris", "FR")
    cache_module.ecrire_cache_court(_resultat("open_meteo"), "Paris", "FR")
    cache_module.ecrire_cache_court(_resultat("weatherapi"), "Paris", "FR")

    cache_module.invalider_cache("Paris", "FR")

    assert redis_fake.exists("meteo:consolidee:paris:fr") == 0
    assert redis_fake.exists("meteo:brut:openweather:paris:fr") == 0
    assert redis_fake.exists("meteo:brut:open_meteo:paris:fr") == 0
    assert redis_fake.exists("meteo:brut:weatherapi:paris:fr") == 0


def test_incrementer_compteur(redis_fake):
    """Trois appels successifs → compteur à 3."""
    cache_module.incrementer_compteur("Paris", "FR")
    cache_module.incrementer_compteur("Paris", "FR")
    cache_module.incrementer_compteur("Paris", "FR")
    assert int(redis_fake.get("meteo:compteur:paris:fr")) == 3


def test_obtenir_villes_populaires_vide(redis_fake):
    """Aucune ville en cache → liste vide."""
    assert cache_module.obtenir_villes_populaires(5) == []


def test_obtenir_villes_populaires_triees(redis_fake):
    """Villes retournées par popularité décroissante."""
    redis_fake.set("meteo:compteur:paris:fr", 5)
    redis_fake.set("meteo:compteur:lome:tg", 12)
    redis_fake.set("meteo:compteur:lyon:fr", 3)

    villes = cache_module.obtenir_villes_populaires(3)

    assert len(villes) == 3
    assert villes[0] == ("lome", "tg")
    assert villes[1] == ("paris", "fr")
    assert villes[2] == ("lyon", "fr")


def test_obtenir_villes_populaires_limite_n(redis_fake):
    """obtenir_villes_populaires(n) ne retourne pas plus de n villes."""
    for i in range(10):
        redis_fake.set(f"meteo:compteur:ville{i}:fr", i)
    assert len(cache_module.obtenir_villes_populaires(3)) == 3


def test_obtenir_villes_populaires_redis_ko(redis_ko):
    """Redis indisponible → liste vide."""
    assert cache_module.obtenir_villes_populaires(5) == []


def test_enregistrer_hit_et_miss(redis_fake):
    """Compteurs hits et misses s'incrémentent correctement."""
    cache_module.enregistrer_hit()
    cache_module.enregistrer_hit()
    cache_module.enregistrer_hit()
    cache_module.enregistrer_miss()

    assert int(redis_fake.get("meteo:stats:hits")) == 3
    assert int(redis_fake.get("meteo:stats:misses")) == 1


def test_obtenir_stats_ratio(redis_fake):
    """3 hits + 1 miss → ratio 75%."""
    redis_fake.set("meteo:stats:hits", 3)
    redis_fake.set("meteo:stats:misses", 1)

    stats = cache_module.obtenir_stats()

    assert stats["hits"] == 3
    assert stats["misses"] == 1
    assert stats["ratio_pct"] == 75


def test_obtenir_stats_sans_donnees(redis_fake):
    """Aucun appel enregistré → ratio 0 sans division par zéro."""
    assert cache_module.obtenir_stats() == {"hits": 0, "misses": 0, "ratio_pct": 0}


def test_obtenir_stats_redis_ko(redis_ko):
    """Redis indisponible → retourne des zéros sans exception."""
    assert cache_module.obtenir_stats() == {"hits": 0, "misses": 0, "ratio_pct": 0}
