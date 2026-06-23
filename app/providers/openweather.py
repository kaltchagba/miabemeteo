# ============================================================
# app/providers/openweather.py — Client OpenWeatherMap
# ============================================================
# Ce module interroge l'API OpenWeatherMap (gratuite, 60 appels/min).
# Documentation de l'API : https://openweathermap.org/current
#
# Concept clé (ch.06 exemple_01_async_route) :
#   On utilise httpx.AsyncClient pour faire des appels HTTP non-bloquants.
#   Cela permet à asyncio.gather() (étape 6) de lancer les 3 providers
#   en parallèle et d'attendre qu'ils soient tous finis simultanément.
# ============================================================

import httpx                        # Client HTTP async (ch.06 du cours)

from app.config import (
    OPENWEATHER_API_KEY,            # Clé API depuis les variables d'env
    OPENWEATHER_BASE_URL,           # URL de base de l'API
    PROVIDER_TIMEOUT,               # Timeout configurable
)
from app.schemas import DonneesMeteo  # Schéma de sortie normalisé (étape 2)


# ============================================================
# CONSTANTE : identifiant unique de ce fournisseur
# Utilisé dans ResultatFournisseur et les logs
# ============================================================
PROVIDER_ID = "openweather"


# ============================================================
# FONCTION PRINCIPALE : fetch()
# ============================================================
async def fetch(client: httpx.AsyncClient, ville: str, pays: str) -> DonneesMeteo:
    """
    Interroge l'API OpenWeatherMap pour une ville donnée.

    Paramètres :
        client  — instance httpx.AsyncClient partagée (créée dans main.py)
                  On la reçoit en paramètre au lieu de la créer ici pour :
                  1. Réutiliser la même connexion TCP (plus rapide)
                  2. Faciliter les tests (on peut injecter un client mocké)
        ville   — nom de la ville (ex: "Paris", "Lomé")
        pays    — code pays ISO 3166-1 alpha-2 (ex: "FR", "TG")

    Retourne :
        DonneesMeteo — données normalisées (même format que les autres providers)

    Lève :
        httpx.HTTPError     — si l'API renvoie une erreur HTTP (4xx, 5xx)
        httpx.TimeoutException — si l'API ne répond pas dans le délai
        ValueError          — si la réponse JSON est malformée
    """

    # ---- Construction des paramètres de la requête ----
    # L'API OpenWeather attend : ?q=Paris,FR&appid=CLE&units=metric&lang=fr
    params = {
        "q":     f"{ville},{pays}",    # Ville + pays pour lever l'ambiguïté
        "appid": OPENWEATHER_API_KEY,  # Clé API (obligatoire)
        "units": "metric",             # Unités métriques → température en °C
        "lang":  "fr",                 # Descriptions en français
    }

    # ---- Appel HTTP asynchrone ----
    # await libère le thread pendant l'attente de la réponse réseau.
    # Les autres coroutines (les 2 autres providers) peuvent s'exécuter
    # pendant ce temps → c'est le cœur du gain de performance async.
    response = await client.get(
        f"{OPENWEATHER_BASE_URL}/weather",  # Endpoint "météo actuelle"
        params=params,                       # Paramètres de query string
        timeout=PROVIDER_TIMEOUT,            # Abandonner après N secondes
    )

    # ---- Vérification du code HTTP ----
    # raise_for_status() lève une httpx.HTTPStatusError si code >= 400
    # Ex : 401 = clé invalide, 404 = ville inconnue, 429 = quota dépassé
    response.raise_for_status()

    # ---- Décodage de la réponse JSON ----
    data = response.json()

    # ---- Extraction et normalisation des données ----
    # La réponse OpenWeather a cette structure (simplifiée) :
    # {
    #   "main": {"temp": 22.5, "humidity": 65},
    #   "wind": {"speed": 4.2},           ← en m/s, on convertit en km/h
    #   "weather": [{"description": "ciel dégagé", "id": 800}]
    # }

    # Température : déjà en °C grâce au paramètre units=metric
    temperature = data["main"]["temp"]

    # Humidité : directement en pourcentage
    humidite = data["main"]["humidity"]

    # Vitesse du vent : en m/s dans l'API OpenWeather → conversion en km/h
    # 1 m/s = 3.6 km/h
    vent_ms = data["wind"]["speed"]        # Vitesse en mètres par seconde
    vent_kmh = round(vent_ms * 3.6, 1)    # Conversion et arrondi à 1 décimale

    # Description : premier élément de la liste "weather"
    # OpenWeather peut retourner plusieurs conditions météo simultanées
    # On prend la première (la principale)
    description = data["weather"][0]["description"]

    # Code météo interne OpenWeather (800 = ciel clair, 500 = pluie légère...)
    # On l'utilise comme code normalisé de référence entre providers
    code_meteo = data["weather"][0]["id"]

    # ---- Construction et retour du schéma normalisé ----
    # Pydantic valide toutes les valeurs au moment de la création
    return DonneesMeteo(
        temperature_c=temperature,
        humidite_pct=humidite,
        vent_kmh=vent_kmh,
        description=description,
        code_meteo=code_meteo,
    )