import httpx

from app.config import (
    OPENWEATHER_API_KEY,
    OPENWEATHER_BASE_URL,
    PROVIDER_TIMEOUT,
)
from app.schemas import DonneesMeteo

PROVIDER_ID = "openweather"


async def fetch(client: httpx.AsyncClient, ville: str, pays: str) -> DonneesMeteo:
    """Interroge l'API OpenWeatherMap pour une ville donnée. Retourne DonneesMeteo normalisé."""
    params = {
        "q":     f"{ville},{pays}",
        "appid": OPENWEATHER_API_KEY,
        "units": "metric",
        "lang":  "fr",
    }

    response = await client.get(
        f"{OPENWEATHER_BASE_URL}/weather",
        params=params,
        timeout=PROVIDER_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()

    vent_kmh = round(data["wind"]["speed"] * 3.6, 1)  # m/s → km/h

    return DonneesMeteo(
        temperature_c=data["main"]["temp"],
        humidite_pct=data["main"]["humidity"],
        vent_kmh=vent_kmh,
        description=data["weather"][0]["description"],
        code_meteo=data["weather"][0]["id"],
    )
