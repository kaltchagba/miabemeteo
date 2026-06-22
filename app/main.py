# ============================================================
# app/main.py — Point d'entrée de l'application FastAPI
# ============================================================
# Ce fichier crée l'instance FastAPI et monte les routers.
# Pour l'instant (étape 1) : squelette minimal qui confirme
# que la structure fonctionne. Les vraies routes seront ajoutées
# aux étapes 6 et 8.
# ============================================================

import os                           # Pour lire les variables d'environnement
from fastapi import FastAPI          # Le framework principal (ch.05 du cours)
from fastapi.responses import JSONResponse  # Pour les réponses JSON manuelles

# ---- Création de l'application FastAPI ----
# Les métadonnées apparaissent dans /docs (Swagger UI)
app = FastAPI(
    title="Agrégateur Météo Multi-Sources",           # Titre affiché dans /docs
    description=(
        "Service qui interroge OpenWeather, Open-Meteo et WeatherAPI "
        "en parallèle, fusionne les résultats et les met en cache Redis."
    ),
    version="1.0.0",                                  # Version de l'API
)


# ---- Route de santé minimale ----
# Présente dès le départ pour que le healthcheck Docker fonctionne
# même avant que les vraies routes soient implémentées.
@app.get(
    "/sante",
    tags=["Infrastructure"],                          # Groupe dans /docs
    summary="Vérifie que l'application répond",
)
def sante():
    """
    Endpoint de santé utilisé par Docker et les load balancers.
    Retourne toujours 200 si l'application tourne.
    Les vrais détails (état Redis, état des fournisseurs) seront
    ajoutés à l'étape 8.
    """
    return {
        "status": "ok",                               # L'app répond
        "version": app.version,                       # Version de l'API
        "note": "Détails des fournisseurs disponibles à l'étape 8",
    }


# ---- Lancement direct (développement) ----
# Permet de lancer avec : python app/main.py
# En production, on utilise le CMD du Dockerfile (uvicorn avec --workers)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",      # Chemin de l'instance FastAPI
        host="127.0.0.1",    # Écoute seulement en local (pas en prod)
        port=8000,           # Port standard
        reload=True,         # Recharge automatique si le code change
    )