from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Clés API (secrètes — masquées dans les logs et le repr)
    openweather_api_key: SecretStr = SecretStr("")
    weatherapi_key: SecretStr = SecretStr("")

    # URLs des fournisseurs
    openweather_base_url: str = "https://api.openweathermap.org/data/2.5"
    open_meteo_geocoding_url: str = "https://geocoding-api.open-meteo.com/v1"
    open_meteo_forecast_url: str = "https://api.open-meteo.com/v1"
    weatherapi_base_url: str = "https://api.weatherapi.com/v1"

    # Redis
    redis_url: str = "redis://localhost:6379"
    cache_court_ttl: int = 300
    cache_long_ttl: int = 3600

    # HTTP
    provider_timeout: float = 5.0

    # Circuit breaker
    circuit_breaker_threshold: int = 3
    circuit_breaker_recovery: int = 30
    circuit_breaker_window: int = 60

    # Scheduler
    top_cities_count: int = 20
    scheduler_interval_minutes: int = 10

    # CORS
    cors_origins: str = "http://localhost:8000"

    # Rate limiting
    rate_limit_requests: int = 100
    rate_limit_window: int = 60
    trusted_proxy: bool = False

    # Seuils d'alerte
    seuil_canicule: float = 35.0
    seuil_gel: float = 0.0
    seuil_vent_fort: float = 80.0
    ttl_alerte: int = 3600

    # Historique
    historique_max_entrees: int = 48


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Constantes exposées au niveau module pour compatibilité des imports existants.
# Les clés API sont exclues : les providers appellent get_settings().*.get_secret_value().
_s = get_settings()

OPENWEATHER_BASE_URL: str = _s.openweather_base_url
OPEN_METEO_GEOCODING_URL: str = _s.open_meteo_geocoding_url
OPEN_METEO_FORECAST_URL: str = _s.open_meteo_forecast_url
WEATHERAPI_BASE_URL: str = _s.weatherapi_base_url

REDIS_URL: str = _s.redis_url
CACHE_COURT_TTL: int = _s.cache_court_ttl
CACHE_LONG_TTL: int = _s.cache_long_ttl

PROVIDER_TIMEOUT: float = _s.provider_timeout

CIRCUIT_BREAKER_THRESHOLD: int = _s.circuit_breaker_threshold
CIRCUIT_BREAKER_RECOVERY: int = _s.circuit_breaker_recovery
CIRCUIT_BREAKER_WINDOW: int = _s.circuit_breaker_window

TOP_CITIES_COUNT: int = _s.top_cities_count
SCHEDULER_INTERVAL_MINUTES: int = _s.scheduler_interval_minutes

CORS_ORIGINS: str = _s.cors_origins

RATE_LIMIT_REQUESTS: int = _s.rate_limit_requests
RATE_LIMIT_WINDOW: int = _s.rate_limit_window
TRUSTED_PROXY: bool = _s.trusted_proxy

SEUIL_CANICULE: float = _s.seuil_canicule
SEUIL_GEL: float = _s.seuil_gel
SEUIL_VENT_FORT: float = _s.seuil_vent_fort
TTL_ALERTE: int = _s.ttl_alerte

HISTORIQUE_MAX_ENTREES: int = _s.historique_max_entrees
