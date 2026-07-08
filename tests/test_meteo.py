import respx
import httpx
from fastapi.testclient import TestClient


OPENWEATHER_PARIS_OK = {
    "main": {"temp": 22.5, "humidity": 60},
    "wind": {"speed": 4.0},  # m/s → 14.4 km/h après conversion
    "weather": [{"description": "ciel dégagé", "id": 800}],
}

OPEN_METEO_GEOCODING_PARIS_OK = {
    "results": [
        {"name": "Paris", "latitude": 48.8534, "longitude": 2.3488, "country": "France"}
    ]
}

OPEN_METEO_FORECAST_PARIS_OK = {
    "hourly": {
        "temperature_2m": [23.0, 22.0, 21.5],
        "relativehumidity_2m": [58, 57, 56],
        "windspeed_10m": [15.0, 14.0, 13.0],
        "weathercode": [0, 0, 1],
    }
}

WEATHERAPI_PARIS_OK = {
    "location": {"name": "Paris", "country": "France"},
    "current": {
        "temp_c": 21.8,
        "humidity": 62,
        "wind_kph": 13.0,
        "condition": {"text": "Ensoleillé", "code": 1000},
    },
}


def _mock_tous_ok():
    respx.get("https://api.openweathermap.org/data/2.5/weather").mock(
        return_value=httpx.Response(200, json=OPENWEATHER_PARIS_OK)
    )
    respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_GEOCODING_PARIS_OK)
    )
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_FORECAST_PARIS_OK)
    )
    respx.get("https://api.weatherapi.com/v1/current.json").mock(
        return_value=httpx.Response(200, json=WEATHERAPI_PARIS_OK)
    )


@respx.mock
def test_succes_complet(client: TestClient):
    """3 providers répondent → 200, 3 sources, moyennes correctes."""
    _mock_tous_ok()

    response = client.get("/meteo", params={"ville": "Paris", "pays": "FR"})

    assert response.status_code == 200
    data = response.json()
    assert data["nb_sources"] == 3
    assert len(data["fournisseurs_ok"]) == 3
    assert len(data["fournisseurs_ko"]) == 0
    assert 21.0 <= data["temperature_c"] <= 24.0
    assert 55 <= data["humidite_pct"] <= 65
    assert data["ville"] == "Paris"
    assert data["pays"] == "FR"


@respx.mock
def test_panne_un_provider(client: TestClient):
    """OpenWeather retourne 503 → réponse 200 avec 2 sources."""
    respx.get("https://api.openweathermap.org/data/2.5/weather").mock(
        return_value=httpx.Response(503, json={"message": "Service indisponible"})
    )
    respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_GEOCODING_PARIS_OK)
    )
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_FORECAST_PARIS_OK)
    )
    respx.get("https://api.weatherapi.com/v1/current.json").mock(
        return_value=httpx.Response(200, json=WEATHERAPI_PARIS_OK)
    )

    response = client.get("/meteo", params={"ville": "Paris", "pays": "FR"})

    assert response.status_code == 200
    data = response.json()
    assert data["nb_sources"] == 2
    assert len(data["fournisseurs_ko"]) == 1
    assert "openweather" in data["fournisseurs_ko"]


@respx.mock
def test_panne_deux_providers(client: TestClient):
    """OpenWeather + WeatherAPI KO → réponse 200 avec 1 seule source (Open-Meteo)."""
    respx.get("https://api.openweathermap.org/data/2.5/weather").mock(
        side_effect=httpx.TimeoutException("Connection timeout")
    )
    respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_GEOCODING_PARIS_OK)
    )
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_FORECAST_PARIS_OK)
    )
    respx.get("https://api.weatherapi.com/v1/current.json").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    response = client.get("/meteo", params={"ville": "Paris", "pays": "FR"})

    assert response.status_code == 200
    data = response.json()
    assert data["nb_sources"] == 1
    assert "open_meteo" in data["fournisseurs_ok"]
    assert data["temperature_c"] == 23.0


@respx.mock
def test_panne_totale(client: TestClient):
    """3 providers KO simultanément → 503."""
    respx.get("https://api.openweathermap.org/data/2.5/weather").mock(
        return_value=httpx.Response(500, json={"message": "Internal Server Error"})
    )
    respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(
        side_effect=httpx.TimeoutException("Timeout")
    )
    respx.get("https://api.weatherapi.com/v1/current.json").mock(
        side_effect=httpx.ConnectError("Connection failed")
    )

    response = client.get("/meteo", params={"ville": "Paris", "pays": "FR"})

    assert response.status_code == 503
    data = response.json()
    assert "detail" in data
    assert len(data["detail"]["fournisseurs_ko"]) == 3


@respx.mock
def test_reponses_incoherentes(client: TestClient):
    """Températures très différentes (10/30/20°C) → la fusion calcule la moyenne exacte."""
    ow_froid = {**OPENWEATHER_PARIS_OK}
    ow_froid["main"] = {**ow_froid["main"], "temp": 10.0, "humidity": 80}

    om_chaud = {
        "hourly": {
            "temperature_2m": [30.0],
            "relativehumidity_2m": [40],
            "windspeed_10m": [5.0],
            "weathercode": [0],
        }
    }

    wa_moyen = {
        "location": {"name": "Paris"},
        "current": {
            "temp_c": 20.0,
            "humidity": 60,
            "wind_kph": 10.0,
            "condition": {"text": "Nuageux", "code": 1006},
        },
    }

    respx.get("https://api.openweathermap.org/data/2.5/weather").mock(
        return_value=httpx.Response(200, json=ow_froid)
    )
    respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_GEOCODING_PARIS_OK)
    )
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=om_chaud)
    )
    respx.get("https://api.weatherapi.com/v1/current.json").mock(
        return_value=httpx.Response(200, json=wa_moyen)
    )

    response = client.get("/meteo", params={"ville": "Paris", "pays": "FR"})

    assert response.status_code == 200
    data = response.json()
    # (10 + 30 + 20) / 3 = 20.0
    assert data["temperature_c"] == 20.0
    assert data["nb_sources"] == 3


@respx.mock
def test_circuit_breaker_souvre(client: TestClient):
    """3 erreurs consécutives sur OpenWeather → circuit OPEN, requêtes suivantes bloquées."""
    from app.circuit_breaker import circuit_breakers, Etat

    cb_ow = circuit_breakers["openweather"]

    respx.get("https://api.openweathermap.org/data/2.5/weather").mock(
        return_value=httpx.Response(500)
    )
    respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_GEOCODING_PARIS_OK)
    )
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_FORECAST_PARIS_OK)
    )
    respx.get("https://api.weatherapi.com/v1/current.json").mock(
        return_value=httpx.Response(200, json=WEATHERAPI_PARIS_OK)
    )

    for i in range(3):
        r = client.get("/meteo", params={"ville": "Paris", "pays": "FR"})
        assert r.status_code == 200

    assert cb_ow.etat == Etat.OPEN

    respx.get("https://api.openweathermap.org/data/2.5/weather").mock(
        side_effect=AssertionError("OpenWeather ne doit pas être appelé (circuit OPEN)")
    )

    r = client.get("/meteo", params={"ville": "Paris", "pays": "FR"})
    assert r.status_code == 200
    data = r.json()
    assert "openweather" in data["fournisseurs_ko"]
    assert data["nb_sources"] == 2


def test_sante_ok(client: TestClient):
    """GET /sante retourne 200 avec les 3 CB en CLOSED."""
    response = client.get("/sante")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert len(data["fournisseurs"]) == 3
    for cb in data["fournisseurs"]:
        assert cb["etat"] == "CLOSED"


def test_ville_requise(client: TestClient):
    """Paramètre ville absent → 422."""
    response = client.get("/meteo")
    assert response.status_code == 422


def test_pays_normalise(client: TestClient):
    """Code pays en minuscules accepté et normalisé en majuscules."""
    with respx.mock:
        _mock_tous_ok()
        response = client.get("/meteo", params={"ville": "Paris", "pays": "fr"})

    assert response.status_code != 422
    if response.status_code == 200:
        assert response.json()["pays"] == "FR"


@respx.mock
def test_comparer_trois_sources(client: TestClient):
    """GET /comparer avec 3 providers OK retourne sources, écarts et indice_consensus."""
    _mock_tous_ok()
    response = client.get("/comparer", params={"ville": "Paris", "pays": "FR"})

    assert response.status_code == 200
    data = response.json()
    assert len(data["sources"]) == 3
    assert "temperature_c" in data["ecarts"]
    assert 0 <= data["indice_consensus"] <= 100
    assert len(data["fournisseurs_ko"]) == 0
    assert data["ville"] == "Paris"
    assert data["pays"] == "FR"


def test_ville_regex_invalide(client: TestClient):
    """Ville contenant des caractères interdits doit retourner 422."""
    response = client.get(
        "/meteo", params={"ville": "<script>alert(1)</script>", "pays": "FR"}
    )
    assert response.status_code == 422


def test_pays_trop_long(client: TestClient):
    """Code pays de plus de 2 caractères doit retourner 422."""
    response = client.get("/meteo", params={"ville": "Paris", "pays": "FRA"})
    assert response.status_code == 422
