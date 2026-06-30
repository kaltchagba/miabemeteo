import httpx

from app.config import (
    OPEN_METEO_GEOCODING_URL,
    OPEN_METEO_FORECAST_URL,
    PROVIDER_TIMEOUT,
)
from app.schemas import DonneesMeteo

PROVIDER_ID = "open_meteo"

# Codes WMO (World Meteorological Organization) → description française
WMO_DESCRIPTIONS: dict[int, str] = {
    0:  "Ciel dégagé",
    1:  "Principalement dégagé",
    2:  "Partiellement nuageux",
    3:  "Couvert",
    45: "Brouillard",
    48: "Brouillard givrant",
    51: "Bruine légère",
    53: "Bruine modérée",
    55: "Bruine dense",
    61: "Pluie légère",
    63: "Pluie modérée",
    65: "Pluie forte",
    71: "Neige légère",
    73: "Neige modérée",
    75: "Neige forte",
    80: "Averses légères",
    81: "Averses modérées",
    82: "Averses violentes",
    95: "Orage",
    96: "Orage avec grêle légère",
    99: "Orage avec grêle forte",
}

CODE_INCONNU_DESCRIPTION = "Conditions inconnues"

# Conversion codes WMO → codes OpenWeather (référence inter-providers)
WMO_VERS_OPENWEATHER: dict[int, int] = {
    0: 800,  1: 800,  2: 802,  3: 804,
    45: 741, 51: 300, 61: 500, 63: 501,
    65: 502, 71: 600, 80: 520, 95: 200,
}


async def _geocoder(client: httpx.AsyncClient, ville: str, pays: str) -> tuple[float, float]:
    """Retourne (latitude, longitude) pour une ville. Lève ValueError si introuvable."""
    response = await client.get(
        f"{OPEN_METEO_GEOCODING_URL}/search",
        params={"name": ville, "count": 1, "language": "fr", "format": "json"},
        timeout=PROVIDER_TIMEOUT,
    )
    response.raise_for_status()
    resultats = response.json().get("results")
    if not resultats:
        raise ValueError(f"Ville introuvable dans Open-Meteo : '{ville}, {pays}'")
    return resultats[0]["latitude"], resultats[0]["longitude"]


async def _get_meteo(client: httpx.AsyncClient, latitude: float, longitude: float) -> DonneesMeteo:
    """Récupère la météo actuelle (première heure) pour des coordonnées GPS."""
    response = await client.get(
        f"{OPEN_METEO_FORECAST_URL}/forecast",
        params={
            "latitude":       latitude,
            "longitude":      longitude,
            "hourly":         "temperature_2m,relativehumidity_2m,windspeed_10m,weathercode",
            "windspeed_unit": "kmh",
            "forecast_days":  1,
            "timezone":       "auto",
        },
        timeout=PROVIDER_TIMEOUT,
    )
    response.raise_for_status()
    hourly = response.json()["hourly"]
    code_wmo = hourly["weathercode"][0]

    return DonneesMeteo(
        temperature_c=hourly["temperature_2m"][0],
        humidite_pct=float(hourly["relativehumidity_2m"][0]),
        vent_kmh=hourly["windspeed_10m"][0],
        description=WMO_DESCRIPTIONS.get(code_wmo, CODE_INCONNU_DESCRIPTION),
        code_meteo=WMO_VERS_OPENWEATHER.get(code_wmo, 0),
    )


async def fetch_previsions(
    client: httpx.AsyncClient,
    ville: str,
    pays: str,
    nb_jours: int = 7,
) -> list[dict]:
    """Retourne les prévisions journalières (min/max/précip/weathercode) sur nb_jours."""
    latitude, longitude = await _geocoder(client, ville, pays)

    response = await client.get(
        f"{OPEN_METEO_FORECAST_URL}/forecast",
        params={
            "latitude":       latitude,
            "longitude":      longitude,
            "daily":          "temperature_2m_max,temperature_2m_min,weathercode,precipitation_sum",
            "windspeed_unit": "kmh",
            "forecast_days":  nb_jours,
            "timezone":       "auto",
        },
        timeout=PROVIDER_TIMEOUT,
    )
    response.raise_for_status()
    daily = response.json()["daily"]

    jours = []
    for i in range(min(nb_jours, len(daily["time"]))):
        code_wmo = daily["weathercode"][i] or 0
        jours.append({
            "date":             daily["time"][i],
            "temp_min":         round(daily["temperature_2m_min"][i] or 0, 1),
            "temp_max":         round(daily["temperature_2m_max"][i] or 0, 1),
            "description":      WMO_DESCRIPTIONS.get(code_wmo, CODE_INCONNU_DESCRIPTION),
            "code_meteo":       WMO_VERS_OPENWEATHER.get(code_wmo, 0),
            "precipitation_mm": round(daily["precipitation_sum"][i] or 0, 1),
        })
    return jours


async def fetch(client: httpx.AsyncClient, ville: str, pays: str) -> DonneesMeteo:
    """Interroge Open-Meteo en deux appels : géocodage puis météo horaire."""
    latitude, longitude = await _geocoder(client, ville, pays)
    return await _get_meteo(client, latitude, longitude)
