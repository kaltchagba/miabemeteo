# ============================================================
# app/providers/weatherapi.py — Client WeatherAPI
# ============================================================
# WeatherAPI est une API météo avec un plan gratuit (1M requêtes/mois).
# Documentation : https://www.weatherapi.com/docs/
#
# Avantage par rapport à OpenWeather :
#   Réponse plus riche (qualité de l'air, UV index, etc.)
#   Unités métriques disponibles directement (temp_c, wind_kph).
#   Pas de conversion d'unités nécessaire côté client.
#
# Concepts utilisés (ch.06 exemple_01_async_route) :
#   - httpx.AsyncClient injecté depuis router_meteo.py
#   - DonneesMeteo normalisé pour être fusionné avec les 2 autres providers
# ============================================================

import httpx

from app.config import (
    WEATHERAPI_KEY,       # Clé API depuis les variables d'environnement
    WEATHERAPI_BASE_URL,  # https://api.weatherapi.com/v1
    PROVIDER_TIMEOUT,
)
from app.schemas import DonneesMeteo

# Identifiant unique de ce fournisseur dans le registre des circuit breakers
PROVIDER_ID = "weatherapi"

# ============================================================
# CORRESPONDANCE CODES WEATHERAPI → CODES OPENWEATHER
# ============================================================
# WeatherAPI utilise ses propres codes de condition.
# On les convertit vers les codes OpenWeather utilisés comme référence commune.
# Source : https://www.weatherapi.com/docs/weather_conditions.json
WEATHERAPI_VERS_OPENWEATHER: dict[int, int] = {
    1000: 800,   # Sunny / Clear              → Clear sky
    1003: 801,   # Partly cloudy              → Few clouds
    1006: 802,   # Cloudy                     → Scattered clouds
    1009: 804,   # Overcast                   → Overcast clouds
    1030: 741,   # Mist                       → Fog
    1063: 500,   # Patchy rain possible       → Light rain
    1066: 600,   # Patchy snow possible       → Light snow
    1072: 301,   # Patchy freezing drizzle    → Drizzle
    1087: 200,   # Thundery outbreaks         → Thunderstorm
    1114: 621,   # Blowing snow               → Snow shower
    1135: 741,   # Fog                        → Fog
    1147: 741,   # Freezing fog               → Fog
    1150: 300,   # Patchy light drizzle       → Light drizzle
    1153: 300,   # Light drizzle              → Light drizzle
    1168: 301,   # Freezing drizzle           → Drizzle
    1171: 302,   # Heavy freezing drizzle     → Heavy drizzle
    1180: 500,   # Patchy light rain          → Light rain
    1183: 500,   # Light rain                 → Light rain
    1186: 501,   # Moderate rain at times     → Moderate rain
    1189: 501,   # Moderate rain              → Moderate rain
    1192: 502,   # Heavy rain at times        → Heavy rain
    1195: 502,   # Heavy rain                 → Heavy rain
    1198: 511,   # Light freezing rain        → Freezing rain
    1201: 511,   # Moderate or heavy freezing rain → Freezing rain
    1204: 611,   # Light sleet                → Sleet
    1207: 612,   # Moderate or heavy sleet    → Sleet
    1210: 600,   # Patchy light snow          → Light snow
    1213: 600,   # Light snow                 → Light snow
    1216: 601,   # Patchy moderate snow       → Snow
    1219: 601,   # Moderate snow              → Snow
    1222: 602,   # Patchy heavy snow          → Heavy snow
    1225: 602,   # Heavy snow                 → Heavy snow
    1237: 511,   # Ice pellets                → Freezing rain
    1240: 520,   # Light rain shower          → Light shower rain
    1243: 521,   # Moderate or heavy rain shower → Shower rain
    1246: 522,   # Torrential rain shower     → Heavy shower rain
    1249: 611,   # Light sleet showers        → Sleet
    1252: 612,   # Moderate or heavy sleet showers → Sleet
    1255: 620,   # Light snow showers         → Light shower snow
    1258: 621,   # Moderate or heavy snow showers → Shower snow
    1261: 511,   # Light showers of ice pellets → Freezing rain
    1264: 511,   # Moderate or heavy showers of ice pellets → Freezing rain
    1273: 200,   # Patchy light rain with thunder → Thunderstorm
    1276: 201,   # Moderate or heavy rain with thunder → Thunderstorm
    1279: 220,   # Patchy light snow with thunder → Thunderstorm with snow
    1282: 221,   # Moderate or heavy snow with thunder → Thunderstorm with snow
}


# ============================================================
# FONCTION PRINCIPALE : fetch()
# ============================================================
async def fetch(client: httpx.AsyncClient, ville: str, pays: str) -> DonneesMeteo:
    """
    Interroge l'API WeatherAPI pour une ville donnée.

    Paramètres :
        client  — instance httpx.AsyncClient partagée (connection pooling)
        ville   — nom de la ville (ex: "Paris", "Lomé")
        pays    — code pays ISO 3166-1 alpha-2 (ex: "FR", "TG")

    Retourne :
        DonneesMeteo — données normalisées (même format que les 2 autres providers)

    Lève :
        httpx.HTTPStatusError  — si l'API renvoie une erreur HTTP (4xx, 5xx)
        httpx.TimeoutException — si l'API ne répond pas dans le délai
        KeyError               — si la réponse JSON est incomplète (structure inattendue)
    """

    # ---- Construction des paramètres de la requête ----
    # WeatherAPI attend : ?key=CLE&q=Paris,FR&lang=fr&aqi=no
    params = {
        "key":  WEATHERAPI_KEY,        # Clé API (obligatoire)
        "q":    f"{ville},{pays}",     # Requête : ville + pays pour éviter l'ambiguïté
        "lang": "fr",                  # Descriptions en français
        "aqi":  "no",                  # Pas d'indice de qualité de l'air (non nécessaire)
    }

    # ---- Appel HTTP asynchrone ----
    response = await client.get(
        f"{WEATHERAPI_BASE_URL}/current.json",   # Endpoint "conditions actuelles"
        params=params,
        timeout=PROVIDER_TIMEOUT,
    )

    # Lève httpx.HTTPStatusError si code HTTP >= 400
    # Ex : 400 = clé invalide, 403 = quota dépassé
    response.raise_for_status()

    # ---- Décodage de la réponse JSON ----
    # Structure WeatherAPI (simplifiée) :
    # {
    #   "location": {"name": "Paris", "country": "France"},
    #   "current": {
    #     "temp_c":   22.0,          ← directement en °C
    #     "humidity": 60,            ← en %
    #     "wind_kph": 14.4,          ← directement en km/h
    #     "condition": {
    #       "text": "Ensoleillé",    ← description en français (grâce à lang=fr)
    #       "code": 1000,            ← code WeatherAPI (→ code OpenWeather via table)
    #     }
    #   }
    # }
    data = response.json()

    current = data["current"]
    condition = current["condition"]

    # ---- Extraction des données (aucune conversion d'unités nécessaire) ----
    temperature = current["temp_c"]          # Déjà en °C
    humidite    = float(current["humidity"]) # En %, converti en float pour cohérence
    vent_kmh    = current["wind_kph"]        # Déjà en km/h

    # Description en français (WeatherAPI retourne les textes selon le paramètre lang)
    description = condition["text"]

    # Conversion du code WeatherAPI en code OpenWeather (normalisation inter-providers)
    code_weatherapi   = condition["code"]
    code_openweather  = WEATHERAPI_VERS_OPENWEATHER.get(code_weatherapi, 0)

    # ---- Construction et retour du schéma normalisé ----
    return DonneesMeteo(
        temperature_c=temperature,
        humidite_pct=humidite,
        vent_kmh=vent_kmh,
        description=description,
        code_meteo=code_openweather,
    )
