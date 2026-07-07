# app/config.py
# Constantes lues depuis les variables d'environnement (fichier .env).
# Copier .env.example → .env et renseigner les clés avant de lancer.

import os

OPENWEATHER_API_KEY: str = os.environ.get("OPENWEATHER_API_KEY", "")
WEATHERAPI_KEY: str = os.environ.get("WEATHERAPI_KEY", "")

OPENWEATHER_BASE_URL: str = "https://api.openweathermap.org/data/2.5"
OPEN_METEO_GEOCODING_URL: str = "https://geocoding-api.open-meteo.com/v1"
OPEN_METEO_FORECAST_URL: str = "https://api.open-meteo.com/v1"
WEATHERAPI_BASE_URL: str = "https://api.weatherapi.com/v1"

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379")
CACHE_COURT_TTL: int = int(os.environ.get("CACHE_COURT_TTL", "300"))  # 5 min
CACHE_LONG_TTL: int = int(os.environ.get("CACHE_LONG_TTL", "3600"))  # 1 h

PROVIDER_TIMEOUT: float = float(os.environ.get("PROVIDER_TIMEOUT", "5.0"))

CIRCUIT_BREAKER_THRESHOLD: int = int(os.environ.get("CIRCUIT_BREAKER_THRESHOLD", "3"))
CIRCUIT_BREAKER_RECOVERY: int = int(os.environ.get("CIRCUIT_BREAKER_RECOVERY", "30"))
CIRCUIT_BREAKER_WINDOW: int = int(os.environ.get("CIRCUIT_BREAKER_WINDOW", "60"))

TOP_CITIES_COUNT: int = int(os.environ.get("TOP_CITIES_COUNT", "20"))
SCHEDULER_INTERVAL_MINUTES: int = int(
    os.environ.get("SCHEDULER_INTERVAL_MINUTES", "10")
)

CORS_ORIGINS: str = os.environ.get("CORS_ORIGINS", "*")

RATE_LIMIT_REQUESTS: int = int(os.environ.get("RATE_LIMIT_REQUESTS", "100"))
RATE_LIMIT_WINDOW: int = int(os.environ.get("RATE_LIMIT_WINDOW", "60"))

SEUIL_CANICULE: float = float(os.environ.get("SEUIL_CANICULE", "35"))
SEUIL_GEL: float = float(os.environ.get("SEUIL_GEL", "0"))
SEUIL_VENT_FORT: float = float(os.environ.get("SEUIL_VENT_FORT", "80"))
TTL_ALERTE: int = int(os.environ.get("TTL_ALERTE", "3600"))

HISTORIQUE_MAX_ENTREES: int = int(os.environ.get("HISTORIQUE_MAX_ENTREES", "48"))
