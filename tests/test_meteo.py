# ============================================================
# tests/test_meteo.py — Tests complets de l'agrégateur météo
# ============================================================
# 6 scénarios testés SANS appel réseau réel (grâce à respx).
#
# respx est un mock pour httpx (comme responses pour requests).
# Il intercepte les appels httpx.AsyncClient.get() et retourne
# des réponses prédéfinies → tests rapides, stables, sans dépendances.
#
# Concepts du cours utilisés :
#   - ch.08 exemple_01_pytest_testclient : TestClient FastAPI
#   - ch.08 exemple_04_tester_auth_et_ml : mocks et fixtures
#   - ch.06 exemple_01_async_route       : asyncio.gather testé via ses effets
#
# Scénarios :
#   1. Succès complet (3 providers répondent)
#   2. Panne d'1 provider (2 répondent)
#   3. Panne de 2 providers (1 répond)
#   4. Panne totale (0 providers → 503)
#   5. Réponses incohérentes entre providers (moyennes correctes)
#   6. Circuit breaker qui s'ouvre après plusieurs erreurs
# ============================================================

import respx                          # Mock httpx (pip install respx)
import httpx

from fastapi.testclient import TestClient


# ============================================================
# DONNÉES DE TEST (réponses JSON simulées des APIs)
# ============================================================

# Réponse simulée d'OpenWeatherMap pour Paris
OPENWEATHER_PARIS_OK = {
    "main": {
        "temp": 22.5,        # °C (déjà en métrique)
        "humidity": 60,      # %
    },
    "wind": {
        "speed": 4.0,        # m/s → 14.4 km/h après conversion (×3.6)
    },
    "weather": [
        {
            "description": "ciel dégagé",
            "id": 800,
        }
    ],
}

# Réponse simulée d'Open-Meteo — Géocodage Paris
OPEN_METEO_GEOCODING_PARIS_OK = {
    "results": [
        {
            "name":      "Paris",
            "latitude":  48.8534,
            "longitude": 2.3488,
            "country":   "France",
        }
    ]
}

# Réponse simulée d'Open-Meteo — Données météo
OPEN_METEO_FORECAST_PARIS_OK = {
    "hourly": {
        "temperature_2m":      [23.0, 22.0, 21.5],   # On prend [0] = heure actuelle
        "relativehumidity_2m": [58,   57,   56],
        "windspeed_10m":       [15.0, 14.0, 13.0],    # Déjà en km/h
        "weathercode":         [0,    0,    1],        # 0 = Ciel dégagé
    }
}

# Réponse simulée de WeatherAPI pour Paris
WEATHERAPI_PARIS_OK = {
    "location": {"name": "Paris", "country": "France"},
    "current": {
        "temp_c":   21.8,
        "humidity": 62,
        "wind_kph": 13.0,
        "condition": {
            "text": "Ensoleillé",
            "code": 1000,   # → OpenWeather code 800
        },
    },
}


# ============================================================
# SCÉNARIO 1 : Succès complet (3/3 providers répondent)
# ============================================================
@respx.mock
def test_succes_complet(client: TestClient):
    """
    Les 3 providers répondent avec succès.
    On vérifie :
    - Code HTTP 200
    - Données fusionnées (moyennes des 3 providers)
    - fournisseurs_ok contient les 3 providers
    - fournisseurs_ko est vide
    """

    # ---- Configuration des mocks respx ----
    # respx intercepte TOUTES les requêtes httpx qui correspondent au pattern

    # Mock OpenWeather
    respx.get("https://api.openweathermap.org/data/2.5/weather").mock(
        return_value=httpx.Response(200, json=OPENWEATHER_PARIS_OK)
    )

    # Mock Open-Meteo géocodage
    respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_GEOCODING_PARIS_OK)
    )

    # Mock Open-Meteo prévisions
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_FORECAST_PARIS_OK)
    )

    # Mock WeatherAPI
    respx.get("https://api.weatherapi.com/v1/current.json").mock(
        return_value=httpx.Response(200, json=WEATHERAPI_PARIS_OK)
    )

    # ---- Appel de l'endpoint ----
    response = client.get("/meteo", params={"ville": "Paris", "pays": "FR"})

    # ---- Vérifications ----
    assert response.status_code == 200, f"Attendu 200, eu {response.status_code}"

    data = response.json()

    # Les 3 providers ont répondu
    assert len(data["fournisseurs_ok"]) == 3,  "Doit avoir 3 fournisseurs OK"
    assert len(data["fournisseurs_ko"]) == 0,  "Doit avoir 0 fournisseur KO"
    assert data["nb_sources"] == 3

    # La température est la moyenne des 3 : (22.5 + 23.0 + 21.8) / 3 = 22.43... ≈ 22.4
    temp = data["temperature_c"]
    assert 21.0 <= temp <= 24.0, f"Température hors plage : {temp}"

    # L'humidité est une moyenne aussi
    assert 55 <= data["humidite_pct"] <= 65, f"Humidité hors plage : {data['humidite_pct']}"

    # La ville et le pays sont correctement répercutés
    assert data["ville"] == "Paris"
    assert data["pays"] == "FR"

    print(f"\n✓ Succès complet — temp={temp}°C, sources={data['fournisseurs_ok']}")


# ============================================================
# SCÉNARIO 2 : Panne d'1 provider (2/3 répondent)
# ============================================================
@respx.mock
def test_panne_un_provider(client: TestClient):
    """
    OpenWeather retourne une erreur 503.
    Les 2 autres providers répondent normalement.
    On vérifie que la réponse est quand même 200 avec 2 sources.
    """

    # OpenWeather en panne (erreur serveur 503)
    respx.get("https://api.openweathermap.org/data/2.5/weather").mock(
        return_value=httpx.Response(503, json={"message": "Service indisponible"})
    )

    # Open-Meteo fonctionne normalement
    respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_GEOCODING_PARIS_OK)
    )
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_FORECAST_PARIS_OK)
    )

    # WeatherAPI fonctionne normalement
    respx.get("https://api.weatherapi.com/v1/current.json").mock(
        return_value=httpx.Response(200, json=WEATHERAPI_PARIS_OK)
    )

    # ---- Appel ----
    response = client.get("/meteo", params={"ville": "Paris", "pays": "FR"})

    # ---- Vérifications ----
    # Service dégradé MAIS disponible → 200 (pas 503)
    assert response.status_code == 200, f"Attendu 200, eu {response.status_code}"

    data = response.json()

    # Seulement 2 sources disponibles
    assert data["nb_sources"] == 2
    assert len(data["fournisseurs_ok"]) == 2
    assert len(data["fournisseurs_ko"]) == 1

    # OpenWeather est bien dans la liste KO
    assert "openweather" in data["fournisseurs_ko"], \
        f"openweather devrait être KO, KO={data['fournisseurs_ko']}"

    print(f"\n✓ Panne 1 provider — {data['fournisseurs_ok']} OK, {data['fournisseurs_ko']} KO")


# ============================================================
# SCÉNARIO 3 : Panne de 2 providers (1/3 répond)
# ============================================================
@respx.mock
def test_panne_deux_providers(client: TestClient):
    """
    OpenWeather et WeatherAPI sont en panne.
    Seul Open-Meteo répond.
    On vérifie qu'on obtient quand même une réponse avec 1 seule source.
    """

    # OpenWeather : timeout simulé
    respx.get("https://api.openweathermap.org/data/2.5/weather").mock(
        side_effect=httpx.TimeoutException("Connection timeout")
    )

    # Open-Meteo : OK
    respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_GEOCODING_PARIS_OK)
    )
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=OPEN_METEO_FORECAST_PARIS_OK)
    )

    # WeatherAPI : erreur réseau simulée
    respx.get("https://api.weatherapi.com/v1/current.json").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    # ---- Appel ----
    response = client.get("/meteo", params={"ville": "Paris", "pays": "FR"})

    # ---- Vérifications ----
    assert response.status_code == 200, f"Attendu 200, eu {response.status_code}"

    data = response.json()

    # 1 seule source disponible
    assert data["nb_sources"] == 1
    assert len(data["fournisseurs_ok"]) == 1
    assert len(data["fournisseurs_ko"]) == 2
    assert "open_meteo" in data["fournisseurs_ok"]

    # Les données viennent uniquement d'Open-Meteo (23.0°C dans notre mock)
    assert data["temperature_c"] == 23.0, \
        f"Attendu 23.0 (Open-Meteo seul), eu {data['temperature_c']}"

    print(f"\n✓ Panne 2 providers — seul {data['fournisseurs_ok']} répond")


# ============================================================
# SCÉNARIO 4 : Panne totale (0/3 providers → 503)
# ============================================================
@respx.mock
def test_panne_totale(client: TestClient):
    """
    Les 3 providers sont simultanément en panne.
    On vérifie que l'API retourne 503 avec un message d'erreur clair.
    C'est le seul scénario où le service est totalement indisponible.
    """

    # Tous les providers en panne
    respx.get("https://api.openweathermap.org/data/2.5/weather").mock(
        return_value=httpx.Response(500, json={"message": "Internal Server Error"})
    )
    respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(
        side_effect=httpx.TimeoutException("Timeout")
    )
    respx.get("https://api.weatherapi.com/v1/current.json").mock(
        side_effect=httpx.ConnectError("Connection failed")
    )

    # ---- Appel ----
    response = client.get("/meteo", params={"ville": "Paris", "pays": "FR"})

    # ---- Vérifications ----
    # Tous en panne → 503 Service Unavailable
    assert response.status_code == 503, f"Attendu 503, eu {response.status_code}"

    data = response.json()

    # Le message d'erreur doit être dans le champ "detail"
    assert "detail" in data
    assert "fournisseurs_ko" in data["detail"]

    # Les 3 providers doivent être dans la liste KO
    ko = data["detail"]["fournisseurs_ko"]
    assert len(ko) == 3, f"Attendu 3 KO, eu {len(ko)} : {ko}"

    print(f"\n✓ Panne totale → 503 — KO : {ko}")


# ============================================================
# SCÉNARIO 5 : Réponses incohérentes (températures très différentes)
# ============================================================
@respx.mock
def test_reponses_incoherentes(client: TestClient):
    """
    Les 3 providers donnent des températures très différentes.
    On vérifie que la fusion calcule bien la MOYENNE (pas le max ou le min).
    Cas réel : différences de capteurs, délais de mise à jour, etc.
    """

    # OpenWeather : 10°C (nuit ?)
    ow_froid = {**OPENWEATHER_PARIS_OK}
    ow_froid["main"] = {**ow_froid["main"], "temp": 10.0, "humidity": 80}

    # Open-Meteo : 30°C (jour ?)
    om_chaud = {
        "hourly": {
            "temperature_2m":      [30.0],
            "relativehumidity_2m": [40],
            "windspeed_10m":       [5.0],
            "weathercode":         [0],
        }
    }

    # WeatherAPI : 20°C (intermédiaire)
    wa_moyen = {
        "location": {"name": "Paris"},
        "current": {
            "temp_c":   20.0,
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

    # ---- Appel ----
    response = client.get("/meteo", params={"ville": "Paris", "pays": "FR"})

    # ---- Vérifications ----
    assert response.status_code == 200

    data = response.json()

    # Moyenne attendue : (10 + 30 + 20) / 3 = 20.0°C
    temp = data["temperature_c"]
    assert temp == 20.0, f"Moyenne attendue 20.0°C, calculée {temp}°C"

    # 3 sources disponibles malgré les incohérences
    assert data["nb_sources"] == 3

    print(f"\n✓ Incohérences fusionnées — temp={temp}°C (10+30+20)/3")


# ============================================================
# SCÉNARIO 6 : Circuit breaker s'ouvre après erreurs répétées
# ============================================================
@respx.mock
def test_circuit_breaker_souvre(client: TestClient):
    """
    On simule des erreurs répétées sur OpenWeather jusqu'à l'ouverture du circuit.
    Après ouverture, les requêtes suivantes ne doivent PLUS appeler OpenWeather
    (le circuit breaker les bloque avant l'appel HTTP).

    C'est le test le plus complexe : on fait plusieurs requêtes successives.
    """
    from app.circuit_breaker import circuit_breakers, Etat

    cb_ow = circuit_breakers["openweather"]   # Circuit breaker OpenWeather

    # ---- Phase 1 : faire échouer OpenWeather N fois (N = seuil) ----
    # Le seuil par défaut est 3 (CIRCUIT_BREAKER_THRESHOLD)

    respx.get("https://api.openweathermap.org/data/2.5/weather").mock(
        return_value=httpx.Response(500)   # Erreur serveur
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

    # 3 requêtes → 3 erreurs OpenWeather → circuit s'ouvre
    for i in range(3):
        r = client.get("/meteo", params={"ville": "Paris", "pays": "FR"})
        assert r.status_code == 200, \
            f"Requête {i+1} : attendu 200 (2 autres sources OK), eu {r.status_code}"

    # ---- Vérification : le circuit OpenWeather est maintenant OPEN ----
    assert cb_ow.etat == Etat.OPEN, \
        f"Circuit devrait être OPEN après 3 erreurs, est {cb_ow.etat}"

    print(f"\n✓ Circuit OpenWeather ouvert après 3 erreurs : {cb_ow.etat}")

    # ---- Phase 2 : nouvelle requête avec circuit OPEN ----
    # Le circuit breaker doit bloquer l'appel sans faire de requête HTTP
    # On réinitialise les mocks pour détecter si OpenWeather est appelé
    respx.get("https://api.openweathermap.org/data/2.5/weather").mock(
        side_effect=AssertionError("OpenWeather NE DOIT PAS être appelé (circuit OPEN !)")
    )

    r = client.get("/meteo", params={"ville": "Paris", "pays": "FR"})

    # La réponse doit être 200 (les 2 autres providers fonctionnent toujours)
    assert r.status_code == 200

    data = r.json()

    # OpenWeather doit être dans la liste KO (bloqué par le circuit breaker)
    assert "openweather" in data["fournisseurs_ko"], \
        f"openweather devrait être KO (circuit OPEN), KO={data['fournisseurs_ko']}"

    # Seulement 2 sources (Open-Meteo + WeatherAPI)
    assert data["nb_sources"] == 2

    print(f"✓ Circuit OPEN bloque OpenWeather — {data['nb_sources']} sources actives")


# ============================================================
# TESTS DE L'ENDPOINT /sante
# ============================================================
def test_sante_ok(client: TestClient):
    """
    GET /sante doit retourner 200 avec les 3 circuit breakers en CLOSED.
    """
    response = client.get("/sante")

    assert response.status_code == 200

    data = response.json()

    # L'application est saine au démarrage
    assert data["status"] == "ok"

    # 3 circuit breakers présents
    assert len(data["fournisseurs"]) == 3

    # Tous en CLOSED au départ (grâce à la fixture reset_circuit_breakers)
    for cb in data["fournisseurs"]:
        assert cb["etat"] == "CLOSED", \
            f"CB {cb['fournisseur']} devrait être CLOSED, est {cb['etat']}"

    print(f"\n✓ /sante — status={data['status']}, {len(data['fournisseurs'])} CB CLOSED")


# ============================================================
# TESTS DE VALIDATION DES PARAMÈTRES
# ============================================================
def test_ville_requise(client: TestClient):
    """La ville est obligatoire → 422 si absente."""
    response = client.get("/meteo")   # Sans paramètre ville
    assert response.status_code == 422, f"Attendu 422, eu {response.status_code}"
    print("\n✓ Ville absente → 422 Unprocessable Entity")


def test_pays_normalise(client: TestClient):
    """Le code pays en minuscules doit être accepté et normalisé."""
    # On intercepte les appels pour éviter de vraiment contacter les APIs
    # mais on ne vérifie que la normalisation (pas les données retournées)
    # Pour ce test, on se contente de vérifier que la requête n'est pas rejetée
    # en 422 (validation Pydantic)
    with respx.mock:
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

        # "fr" en minuscules → doit être accepté (normalisé en "FR" par le router)
        response = client.get("/meteo", params={"ville": "Paris", "pays": "fr"})

    # Soit 200 (normalisé et traité), soit autre chose mais pas 422
    assert response.status_code != 422, \
        "Le code pays en minuscules ne doit pas lever une erreur de validation"

    if response.status_code == 200:
        assert response.json()["pays"] == "FR"   # Bien normalisé en majuscules

    print(f"\n✓ Code pays 'fr' accepté → pays='{response.json().get('pays', '?')}'")
