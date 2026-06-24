# ============================================================
# app/config.py — Configuration centralisée via variables d'env
# ============================================================
# Toutes les constantes et paramètres de l'app viennent d'ici.
# On utilise os.environ.get() pour lire le fichier .env (ch.10).
# Règle d'or : AUCUN secret ni paramètre en dur dans le code.
# ============================================================

import os   # Module standard Python pour accéder aux variables d'environnement


# ============================================================
# CLÉS API des fournisseurs météo
# ============================================================

# Clé pour OpenWeatherMap (obligatoire pour ce provider)
# Obtenir sur https://openweathermap.org/api (gratuit)
OPENWEATHER_API_KEY: str = os.environ.get("OPENWEATHER_API_KEY", "")

# Clé pour WeatherAPI (obligatoire pour ce provider)
# Obtenir sur https://www.weatherapi.com/ (gratuit)
WEATHERAPI_KEY: str = os.environ.get("WEATHERAPI_KEY", "")

# Open-Meteo ne nécessite aucune clé API (100% gratuit et public)


# ============================================================
# URLs DE BASE des fournisseurs météo
# ============================================================

# URL racine de l'API OpenWeatherMap (version 2.5)
OPENWEATHER_BASE_URL: str = "https://api.openweathermap.org/data/2.5"

# URL racine de l'API Open-Meteo (geocoding pour convertir ville -> coords)
OPEN_METEO_GEOCODING_URL: str = "https://geocoding-api.open-meteo.com/v1"

# URL racine de l'API Open-Meteo (prévisions météo)
OPEN_METEO_FORECAST_URL: str = "https://api.open-meteo.com/v1"

# URL racine de WeatherAPI
WEATHERAPI_BASE_URL: str = "https://api.weatherapi.com/v1"


# ============================================================
# CONFIGURATION REDIS
# ============================================================

# URL de connexion Redis
# En local sans Docker : redis://localhost:6379
# En Docker Compose    : redis://redis:6379 (nom du service docker)
REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379")

# Durée de vie du cache court (données brutes par fournisseur) en secondes
# 300 secondes = 5 minutes
CACHE_COURT_TTL: int = int(os.environ.get("CACHE_COURT_TTL", "300"))

# Durée de vie du cache long (réponse consolidée) en secondes
# 3600 secondes = 1 heure
CACHE_LONG_TTL: int = int(os.environ.get("CACHE_LONG_TTL", "3600"))


# ============================================================
# CONFIGURATION DES PROVIDERS HTTP
# ============================================================

# Délai max en secondes pour qu'un fournisseur réponde
# Au-delà, la requête est abandonnée (timeout)
PROVIDER_TIMEOUT: float = float(os.environ.get("PROVIDER_TIMEOUT", "5.0"))


# ============================================================
# CONFIGURATION DU CIRCUIT BREAKER
# ============================================================

# Nombre d'erreurs consécutives avant d'ouvrir le circuit (passer en OPEN)
# Exemple : 3 erreurs → le circuit s'ouvre, on arrête d'appeler le provider
CIRCUIT_BREAKER_THRESHOLD: int = int(
    os.environ.get("CIRCUIT_BREAKER_THRESHOLD", "3")
)

# Durée en secondes pendant laquelle le circuit reste OPEN
# Après ce délai, il passe en HALF_OPEN pour tester si le provider est revenu
CIRCUIT_BREAKER_RECOVERY: int = int(
    os.environ.get("CIRCUIT_BREAKER_RECOVERY", "30")
)

# Fenêtre de temps (en secondes) sur laquelle on compte les erreurs
# Exemple : 60 secondes → on oublie les erreurs vieilles de plus d'1 minute
CIRCUIT_BREAKER_WINDOW: int = int(
    os.environ.get("CIRCUIT_BREAKER_WINDOW", "60")
)


# ============================================================
# CONFIGURATION DU SCHEDULER (pré-chauffe du cache)
# ============================================================

# Nombre de villes populaires à pré-chauffer automatiquement
TOP_CITIES_COUNT: int = int(os.environ.get("TOP_CITIES_COUNT", "20"))

# Intervalle en minutes entre chaque pré-chauffe
SCHEDULER_INTERVAL_MINUTES: int = int(
    os.environ.get("SCHEDULER_INTERVAL_MINUTES", "10")
)


# ============================================================
# CONFIGURATION CORS
# ============================================================

# Origines autorisées à interroger l'API depuis un navigateur.
# Plusieurs origines séparées par une virgule.
# Exemple : http://localhost:3000,https://mon-app.fr
# La valeur "*" autorise toutes les origines (déconseillé en production).
CORS_ORIGINS: str = os.environ.get("CORS_ORIGINS", "*")