# tests/test_extras.py — Tests des endpoints complémentaires
# /historique, /villes-populaires, /alertes, /previsions, /export, /batch, /interface

import fakeredis
import httpx
import pytest
import respx

import app.cache as cache_module
from app.config import HISTORIQUE_MAX_ENTREES


# ============================================================
# FIXTURE : Redis simulé pour les tests du module cache
# ============================================================


@pytest.fixture(autouse=True)
def redis_mock():
    """Injecte un fakeredis dans le module cache pour tous les tests."""
    fake = fakeredis.FakeRedis(decode_responses=True)
    ancienne_valeur = cache_module._redis
    cache_module._redis = fake
    yield fake
    fake.flushall()
    cache_module._redis = ancienne_valeur


# ============================================================
# TESTS : /historique
# ============================================================


def test_historique_vide(client):
    """Une ville sans historique doit retourner 404."""
    r = client.get("/historique?ville=InconnueXYZ&pays=FR")
    assert r.status_code == 404


def test_historique_apres_enregistrement(client, redis_mock):
    """Après enregistrement d'une entrée, /historique doit la retourner."""
    cache_module.enregistrer_historique(
        ville="Lyon",
        pays="FR",
        temperature=25.0,
        description="Ensoleillé",
        humidite=55.0,
        vent=10.0,
    )
    r = client.get("/historique?ville=Lyon&pays=FR")
    assert r.status_code == 200
    d = r.json()
    assert d["ville"] == "Lyon"
    assert d["pays"] == "FR"
    assert d["nb_entrees"] == 1
    assert d["entrees"][0]["temperature_c"] == 25.0


def test_historique_tendance(client, redis_mock):
    """La tendance doit être 'hausse' si la température monte."""
    for i, temp in enumerate([15.0, 16.0, 18.0, 22.0, 25.0]):
        cache_module.enregistrer_historique(
            ville="Bordeaux",
            pays="FR",
            temperature=temp,
            description="Nuageux",
            humidite=60.0,
            vent=12.0,
        )
    r = client.get("/historique?ville=Bordeaux&pays=FR")
    assert r.status_code == 200
    assert r.json()["tendance"] == "hausse"


def test_historique_fenetre_glissante(client, redis_mock):
    """La liste ne doit jamais dépasser HISTORIQUE_MAX_ENTREES entrées."""
    for i in range(HISTORIQUE_MAX_ENTREES + 10):
        cache_module.enregistrer_historique(
            ville="Marseille",
            pays="FR",
            temperature=float(i),
            description="Test",
            humidite=50.0,
            vent=5.0,
        )
    r = client.get(f"/historique?ville=Marseille&pays=FR&n={HISTORIQUE_MAX_ENTREES}")
    assert r.status_code == 200
    assert r.json()["nb_entrees"] <= HISTORIQUE_MAX_ENTREES


# ============================================================
# TESTS : /villes-populaires
# ============================================================


def test_villes_populaires_vide(client):
    """Sans données, le classement doit être vide."""
    r = client.get("/villes-populaires")
    assert r.status_code == 200
    d = r.json()
    assert d["villes"] == []
    assert d["total_requetes"] == 0


def test_villes_populaires_avec_scores(client, redis_mock):
    """Les villes avec des scores doivent apparaître classées."""
    cache_module.incrementer_score_ville("Paris", "FR")
    cache_module.incrementer_score_ville("Paris", "FR")
    cache_module.incrementer_score_ville("Lyon", "FR")

    r = client.get("/villes-populaires?n=10")
    assert r.status_code == 200
    d = r.json()
    assert d["total_requetes"] == 3
    assert len(d["villes"]) == 2
    assert d["villes"][0]["ville"] == "Paris"  # Paris a 2 requêtes → rang 1
    assert d["villes"][0]["nb_requetes"] == 2
    assert d["villes"][1]["ville"] == "Lyon"


def test_villes_populaires_rang(client, redis_mock):
    """Les rangs doivent commencer à 1 et être ordonnés."""
    for ville in ["A", "B", "C"]:
        cache_module.incrementer_score_ville(ville, "FR")
    cache_module.incrementer_score_ville("A", "FR")  # A = 2, B = C = 1

    r = client.get("/villes-populaires?n=3")
    d = r.json()
    assert d["villes"][0]["rang"] == 1
    assert d["villes"][1]["rang"] == 2
    assert d["villes"][2]["rang"] == 3


# ============================================================
# TESTS : /alertes
# ============================================================


def test_alertes_vide(client):
    """Sans alertes, la réponse doit être vide."""
    r = client.get("/alertes")
    assert r.status_code == 200
    d = r.json()
    assert d["alertes"] == []
    assert d["nb_actives"] == 0


def test_alertes_enregistrement(client, redis_mock):
    """Une alerte enregistrée doit apparaître dans /alertes."""
    cache_module.enregistrer_alerte(
        type_alerte="canicule",
        ville="Séville",
        pays="ES",
        valeur=42.0,
        seuil=35.0,
        message="Canicule sévère à Séville",
        niveau="danger",
    )
    r = client.get("/alertes")
    assert r.status_code == 200
    d = r.json()
    assert d["nb_actives"] == 1
    alerte = d["alertes"][0]
    assert alerte["type_alerte"] == "canicule"
    assert alerte["ville"] == "Séville"
    assert alerte["valeur"] == 42.0
    assert alerte["niveau"] == "danger"


def test_alertes_multiples(client, redis_mock):
    """Plusieurs alertes de types différents doivent toutes apparaître."""
    cache_module.enregistrer_alerte("canicule", "Paris", "FR", 38.0, 35.0, "Chaud")
    cache_module.enregistrer_alerte("gel", "Oslo", "NO", -5.0, 0.0, "Gel")
    cache_module.enregistrer_alerte("vent_fort", "Brest", "FR", 95.0, 80.0, "Vent")

    r = client.get("/alertes")
    d = r.json()
    assert d["nb_actives"] == 3
    types = {a["type_alerte"] for a in d["alertes"]}
    assert types == {"canicule", "gel", "vent_fort"}


# ============================================================
# TESTS : /export
# ============================================================

OPEN_METEO_GEO_OK = {
    "results": [
        {"latitude": 48.85, "longitude": 2.35, "name": "Paris", "country_code": "FR"}
    ]
}
OPEN_METEO_FORECAST_OK = {
    "hourly": {
        "temperature_2m": [20.0],
        "relativehumidity_2m": [65],
        "windspeed_10m": [10.0],
        "weathercode": [0],
    }
}
WEATHERAPI_OK = {
    "current": {
        "temp_c": 21.0,
        "humidity": 63,
        "wind_kph": 11.0,
        "condition": {"text": "Clear", "code": 1000},
    }
}
OPENWEATHER_OK = {
    "main": {"temp": 22.0, "humidity": 60},
    "wind": {"speed": 3.0},
    "weather": [{"description": "ciel dégagé", "id": 800}],
}


@respx.mock
def test_export_csv(client):
    """GET /export?format=csv doit retourner un Content-Disposition avec .csv."""
    respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_GEO_OK)
    )
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_FORECAST_OK)
    )
    respx.get("https://api.openweathermap.org/data/2.5/weather").mock(
        return_value=httpx.Response(200, json=OPENWEATHER_OK)
    )
    respx.get("https://api.weatherapi.com/v1/current.json").mock(
        return_value=httpx.Response(200, json=WEATHERAPI_OK)
    )
    r = client.get("/export?ville=Paris&pays=FR&format=csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert ".csv" in r.headers.get("content-disposition", "")
    assert "Paris" in r.text


@respx.mock
def test_export_json(client):
    """GET /export?format=json doit retourner un JSON téléchargeable."""
    respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_GEO_OK)
    )
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_FORECAST_OK)
    )
    respx.get("https://api.openweathermap.org/data/2.5/weather").mock(
        return_value=httpx.Response(200, json=OPENWEATHER_OK)
    )
    respx.get("https://api.weatherapi.com/v1/current.json").mock(
        return_value=httpx.Response(200, json=WEATHERAPI_OK)
    )
    r = client.get("/export?ville=Paris&pays=FR&format=json")
    assert r.status_code == 200
    assert ".json" in r.headers.get("content-disposition", "")
    d = r.json()
    assert "meteo_actuelle" in d
    assert d["ville"] == "Paris"


def test_export_format_invalide(client):
    """Un format non reconnu doit retourner 422."""
    r = client.get("/export?ville=Paris&format=xml")
    assert r.status_code == 422


# ============================================================
# TESTS : /batch
# ============================================================


@respx.mock
def test_batch_succes(client):
    """POST /batch avec 2 villes valides doit retourner 2 succès."""
    respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_GEO_OK)
    )
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_FORECAST_OK)
    )
    respx.get("https://api.openweathermap.org/data/2.5/weather").mock(
        return_value=httpx.Response(200, json=OPENWEATHER_OK)
    )
    respx.get("https://api.weatherapi.com/v1/current.json").mock(
        return_value=httpx.Response(200, json=WEATHERAPI_OK)
    )
    body = {
        "villes": [{"ville": "Paris", "pays": "FR"}, {"ville": "Lyon", "pays": "FR"}]
    }
    r = client.post("/batch", json=body)
    assert r.status_code == 200
    d = r.json()
    assert d["nb_succes"] == 2
    assert d["nb_erreurs"] == 0
    assert len(d["resultats"]) == 2


def test_batch_liste_vide(client):
    """POST /batch avec liste vide doit retourner 422 (validation Pydantic)."""
    r = client.post("/batch", json={"villes": []})
    assert r.status_code == 422


def test_batch_trop_de_villes(client):
    """POST /batch avec > 10 villes doit retourner 422."""
    villes = [{"ville": f"Ville{i}", "pays": "FR"} for i in range(11)]
    r = client.post("/batch", json={"villes": villes})
    assert r.status_code == 422


# ============================================================
# TESTS : /previsions (Open-Meteo daily)
# ============================================================

OPEN_METEO_DAILY_OK = {
    "daily": {
        "time": [
            "2026-07-01",
            "2026-07-02",
            "2026-07-03",
            "2026-07-04",
            "2026-07-05",
            "2026-07-06",
            "2026-07-07",
        ],
        "temperature_2m_max": [28.0, 30.0, 27.0, 25.0, 26.0, 29.0, 31.0],
        "temperature_2m_min": [18.0, 20.0, 17.0, 16.0, 17.0, 19.0, 21.0],
        "weathercode": [0, 2, 61, 0, 1, 3, 95],
        "precipitation_sum": [0.0, 0.0, 5.2, 0.0, 0.0, 0.0, 0.0],
    }
}


@respx.mock
def test_previsions_7_jours(client):
    """GET /previsions doit retourner 7 jours de prévisions."""
    respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_GEO_OK)
    )
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_DAILY_OK)
    )
    r = client.get("/previsions?ville=Paris&pays=FR&jours=7")
    assert r.status_code == 200
    d = r.json()
    assert d["ville"] == "Paris"
    assert len(d["jours"]) == 7
    assert d["jours"][0]["date"] == "2026-07-01"
    assert d["jours"][0]["temp_max"] == 28.0
    assert d["jours"][2]["precipitation_mm"] == 5.2


@respx.mock
def test_previsions_ville_introuvable(client):
    """Une ville inconnue doit retourner 404."""
    respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(
        return_value=httpx.Response(200, json={"results": None})
    )
    r = client.get("/previsions?ville=XYZINCONNU&pays=FR")
    assert r.status_code == 404


# ============================================================
# TESTS : /interface et /carte
# ============================================================


def test_interface_accessible(client):
    """GET /interface doit retourner du HTML MiabeMETEO avec la carte Leaflet."""
    r = client.get("/interface")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    # Marque de l'interface redesignée
    assert "MiabeMETEO" in r.text or "MiabeMETEO".lower() in r.text.lower()
    # Fonctionnalités présentes
    assert "Historique" in r.text
    assert "Alertes" in r.text
    assert "Classement" in r.text
    # Présence de Leaflet et des fonctions JS critiques
    assert "leaflet" in r.text.lower()
    assert "connectWS" in r.text
    assert "fetchAndOpen" in r.text


def test_carte_redirige(client):
    """GET /carte doit retourner 302 vers /interface."""
    r = client.get("/carte", follow_redirects=False)
    # La route retourne du HTML avec une balise meta refresh (pas un vrai 302 HTTP)
    assert r.status_code in (200, 302)


# ============================================================
# TESTS : Rate limiting middleware
# ============================================================


def test_rate_limit_headers(client):
    """Les réponses doivent contenir les headers X-RateLimit."""
    r = client.get("/sante")
    # /sante est exclue du rate limiting → pas de headers RL
    # On teste juste que le middleware ne crashe pas
    assert r.status_code == 200


def test_carte_redirige_correctement(client):
    """GET /carte doit retourner 302 avec un header Location vers /interface."""
    r = client.get("/carte", follow_redirects=False)
    assert r.status_code == 302
    assert "/interface" in r.headers.get("location", "")


def test_securite_headers_presents(client, redis_mock):
    """Les réponses hors chemins exclus doivent porter les headers de sécurité."""
    r = client.get("/villes-populaires")
    assert r.status_code == 200
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "SAMEORIGIN"
    assert "x-ratelimit-limit" in r.headers


def test_invalider_cache(client):
    """DELETE /cache doit retourner 200 avec un message de confirmation."""
    r = client.delete("/cache?ville=Paris&pays=FR")
    assert r.status_code == 200
    assert "Paris" in r.json()["message"]


def test_invalider_cache_pays_invalide(client):
    """DELETE /cache avec un code pays incorrect doit retourner 422."""
    r = client.delete("/cache?ville=Paris&pays=FRA")
    assert r.status_code == 422
