# ============================================================
# app/providers/open_meteo.py — Client Open-Meteo
# ============================================================
# Open-Meteo est une API météo gratuite et sans clé API.
# Documentation : https://open-meteo.com/en/docs
#
# Particularité : l'API ne prend pas un nom de ville en entrée,
# elle prend des coordonnées GPS (latitude, longitude).
# On doit donc faire 2 appels :
#   1. Geocoding : "Paris, FR" → latitude=48.85, longitude=2.35
#   2. Météo     : lat=48.85&lon=2.35 → données météo
#
# C'est un exemple concret de pourquoi l'async est utile :
# si on avait 3 villes à interroger, les 6 appels pourraient
# se faire en parallèle avec asyncio.gather.
# ============================================================

import httpx                         # Client HTTP async (ch.06 du cours)

from app.config import (
    OPEN_METEO_GEOCODING_URL,        # URL de l'API de géocodage
    OPEN_METEO_FORECAST_URL,         # URL de l'API météo
    PROVIDER_TIMEOUT,                # Timeout configurable
)
from app.schemas import DonneesMeteo  # Schéma de sortie normalisé


# Identifiant unique de ce fournisseur
PROVIDER_ID = "open_meteo"


# ============================================================
# CORRESPONDANCE CODES MÉTÉO OPEN-METEO → DESCRIPTION
# ============================================================
# Open-Meteo utilise les codes WMO (World Meteorological Organization).
# Source : https://open-meteo.com/en/docs#weathervariables
# On les traduit manuellement en descriptions françaises.
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

# Code WMO utilisé quand le code reçu est inconnu
CODE_INCONNU_DESCRIPTION = "Conditions inconnues"


# ============================================================
# CORRESPONDANCE CODES WMO → CODES OPENWEATHER
# ============================================================
# Pour normaliser les codes météo entre providers (schéma MeteoResponse),
# on convertit les codes WMO vers le format OpenWeather utilisé comme référence.
WMO_VERS_OPENWEATHER: dict[int, int] = {
    0: 800,   # Ciel dégagé → Clear sky
    1: 800,   # Principalement dégagé → Clear sky
    2: 802,   # Partiellement nuageux → Few clouds
    3: 804,   # Couvert → Overcast
    45: 741,  # Brouillard
    51: 300,  # Bruine légère → Light drizzle
    61: 500,  # Pluie légère → Light rain
    63: 501,  # Pluie modérée → Moderate rain
    65: 502,  # Pluie forte → Heavy rain
    71: 600,  # Neige légère → Light snow
    80: 520,  # Averses → Light shower rain
    95: 200,  # Orage → Thunderstorm
}


# ============================================================
# ÉTAPE 1 : Géocodage — ville/pays → coordonnées GPS
# ============================================================
async def _geocoder(
    client: httpx.AsyncClient,
    ville: str,
    pays: str,
) -> tuple[float, float]:
    """
    Convertit un nom de ville en coordonnées GPS (latitude, longitude).

    Retourne :
        tuple (latitude, longitude) en degrés décimaux
        Ex : ("Paris", "FR") → (48.8534, 2.3488)

    Lève :
        ValueError — si la ville n'est pas trouvée
    """

    # Paramètres de l'API de géocodage Open-Meteo
    params = {
        "name":     ville,    # Nom de la ville en texte libre
        "count":    1,        # On ne veut que le premier résultat (le plus pertinent)
        "language": "fr",     # Réponses en français
        "format":   "json",   # Format de la réponse
    }

    # Appel asynchrone à l'API de géocodage
    response = await client.get(
        f"{OPEN_METEO_GEOCODING_URL}/search",
        params=params,
        timeout=PROVIDER_TIMEOUT,
    )
    response.raise_for_status()   # Lève une exception si code HTTP >= 400

    data = response.json()

    # L'API retourne {"results": [...]} ou {"results": null} si non trouvé
    resultats = data.get("results")
    if not resultats:
        # Ville inconnue : on lève une ValueError pour que le circuit breaker
        # ne la compte PAS comme une erreur réseau (c'est une erreur client)
        raise ValueError(f"Ville introuvable dans Open-Meteo : '{ville}, {pays}'")

    # On prend le premier résultat (le plus pertinent selon l'API)
    premier = resultats[0]

    # Extraction de la latitude et longitude
    latitude = premier["latitude"]
    longitude = premier["longitude"]

    return latitude, longitude


# ============================================================
# ÉTAPE 2 : Météo — coordonnées GPS → données météo
# ============================================================
async def _get_meteo(
    client: httpx.AsyncClient,
    latitude: float,
    longitude: float,
) -> DonneesMeteo:
    """
    Récupère les données météo actuelles pour des coordonnées GPS.

    L'API Open-Meteo retourne des prévisions horaires. On prend
    la première valeur (heure actuelle) pour chaque variable.
    """

    # Paramètres de l'API météo Open-Meteo
    params = {
        "latitude":           latitude,
        "longitude":          longitude,
        # Variables horaires demandées (séparées par des virgules)
        "hourly":             "temperature_2m,relativehumidity_2m,windspeed_10m,weathercode",
        "windspeed_unit":     "kmh",       # Vitesse du vent directement en km/h
        "forecast_days":      1,           # On ne veut que aujourd'hui
        "timezone":           "auto",      # Fuseau horaire automatique selon les coords
    }

    # Appel asynchrone à l'API météo
    response = await client.get(
        f"{OPEN_METEO_FORECAST_URL}/forecast",
        params=params,
        timeout=PROVIDER_TIMEOUT,
    )
    response.raise_for_status()

    data = response.json()

    # Structure de la réponse Open-Meteo (simplifiée) :
    # {
    #   "hourly": {
    #     "temperature_2m":        [22.1, 21.8, ...],  ← une valeur par heure
    #     "relativehumidity_2m":   [65,   66,   ...],
    #     "windspeed_10m":         [12.0, 11.5, ...],
    #     "weathercode":           [0,    1,    ...],
    #   }
    # }
    # On prend l'index [0] = première heure = heure actuelle

    hourly = data["hourly"]   # Dictionnaire des variables horaires

    # Température en °C (à 2 mètres du sol, standard météorologique)
    temperature = hourly["temperature_2m"][0]

    # Humidité relative en %
    humidite = hourly["relativehumidity_2m"][0]

    # Vitesse du vent en km/h (déjà en km/h grâce au paramètre windspeed_unit)
    vent_kmh = hourly["windspeed_10m"][0]

    # Code WMO de la condition météo
    code_wmo = hourly["weathercode"][0]

    # Conversion du code WMO en description française
    description = WMO_DESCRIPTIONS.get(code_wmo, CODE_INCONNU_DESCRIPTION)

    # Conversion du code WMO en code OpenWeather (normalisation inter-providers)
    code_openweather = WMO_VERS_OPENWEATHER.get(code_wmo, 0)

    # Construction et retour du schéma normalisé
    return DonneesMeteo(
        temperature_c=temperature,
        humidite_pct=float(humidite),    # L'API retourne un int, on cast en float
        vent_kmh=vent_kmh,
        description=description,
        code_meteo=code_openweather,
    )


# ============================================================
# FONCTION PRINCIPALE : fetch()
# ============================================================
async def fetch(client: httpx.AsyncClient, ville: str, pays: str) -> DonneesMeteo:
    """
    Interroge Open-Meteo en deux étapes : géocodage puis météo.

    Paramètres :
        client  — instance httpx.AsyncClient partagée (injection de dépendance)
        ville   — nom de la ville
        pays    — code pays ISO 3166-1 alpha-2

    Retourne :
        DonneesMeteo — données normalisées

    Lève :
        httpx.HTTPError — erreur réseau ou HTTP
        ValueError      — ville introuvable
    """

    # Étape 1 : convertir "Paris, FR" en (48.85, 2.35)
    latitude, longitude = await _geocoder(client, ville, pays)

    # Étape 2 : récupérer la météo à ces coordonnées
    # await garantit que l'étape 1 est terminée avant de commencer l'étape 2
    donnees = await _get_meteo(client, latitude, longitude)

    return donnees