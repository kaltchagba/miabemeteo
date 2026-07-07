import httpx

from app.config import (
    WEATHERAPI_KEY,
    WEATHERAPI_BASE_URL,
    PROVIDER_TIMEOUT,
)
from app.schemas import DonneesMeteo

PROVIDER_ID = "weatherapi"

# Codes WeatherAPI → codes OpenWeather (référence commune inter-providers)
WEATHERAPI_VERS_OPENWEATHER: dict[int, int] = {
    1000: 800,
    1003: 801,
    1006: 802,
    1009: 804,
    1030: 741,
    1063: 500,
    1066: 600,
    1072: 301,
    1087: 200,
    1114: 621,
    1135: 741,
    1147: 741,
    1150: 300,
    1153: 300,
    1168: 301,
    1171: 302,
    1180: 500,
    1183: 500,
    1186: 501,
    1189: 501,
    1192: 502,
    1195: 502,
    1198: 511,
    1201: 511,
    1204: 611,
    1207: 612,
    1210: 600,
    1213: 600,
    1216: 601,
    1219: 601,
    1222: 602,
    1225: 602,
    1237: 511,
    1240: 520,
    1243: 521,
    1246: 522,
    1249: 611,
    1252: 612,
    1255: 620,
    1258: 621,
    1261: 511,
    1264: 511,
    1273: 200,
    1276: 201,
    1279: 220,
    1282: 221,
}


async def fetch(client: httpx.AsyncClient, ville: str, pays: str) -> DonneesMeteo:
    """Interroge l'API WeatherAPI pour une ville donnée. Retourne DonneesMeteo normalisé."""
    response = await client.get(
        f"{WEATHERAPI_BASE_URL}/current.json",
        params={
            "key": WEATHERAPI_KEY,
            "q": f"{ville},{pays}",
            "lang": "fr",
            "aqi": "no",
        },
        timeout=PROVIDER_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()

    current = data["current"]
    condition = current["condition"]
    code_openweather = WEATHERAPI_VERS_OPENWEATHER.get(condition["code"], 0)

    return DonneesMeteo(
        temperature_c=current["temp_c"],
        humidite_pct=float(current["humidity"]),
        vent_kmh=current["wind_kph"],
        description=condition["text"],
        code_meteo=code_openweather,
    )
