# ============================================================
# Dockerfile — Image Docker de l'application météo
# ============================================================
# Construction en deux étapes (multi-stage build) :
#   1. Étape "builder" : installe les dépendances dans un venv
#   2. Étape "runtime" : copie seulement le nécessaire
# Résultat : image finale légère, sans les outils de build.
# ============================================================

# ------ ÉTAPE 1 : BUILDER ------
# On part d'une image Python 3.11 complète pour installer les deps.
FROM python:3.11-slim AS builder

# Définit le dossier de travail dans le conteneur
WORKDIR /app

# Copie uniquement requirements.txt en premier.
# Docker met en cache cette couche : si requirements.txt n'a pas changé,
# pip install ne sera PAS relancé au prochain build. Gain de temps.
COPY requirements.txt .

# Installe les dépendances dans un environnement virtuel isolé.
# --no-cache-dir : ne stocke pas le cache pip (réduit la taille de l'image)
# --prefix=/install : installe dans /install (facile à copier ensuite)
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ------ ÉTAPE 2 : RUNTIME ------
# Image finale : beaucoup plus légère (pas de pip, pas de compilateurs)
FROM python:3.11-slim AS runtime

# Crée un utilisateur non-root pour la sécurité.
# En production, on ne tourne jamais en root dans un conteneur.
RUN useradd --create-home --shell /bin/bash appuser

# Définit le dossier de travail
WORKDIR /app

# Copie les dépendances installées depuis l'étape builder
COPY --from=builder /install /usr/local

# Copie le code source de l'application
COPY app/ ./app/

# Change le propriétaire des fichiers pour l'utilisateur non-root
RUN chown -R appuser:appuser /app

# Bascule sur l'utilisateur non-root
USER appuser

# Expose le port sur lequel uvicorn va écouter
EXPOSE 8000

# Commande de lancement.
# --host 0.0.0.0 : écoute sur toutes les interfaces (nécessaire en Docker)
# --workers 2    : 2 processus uvicorn pour mieux absorber la charge
# --no-access-log: les logs d'accès sont gérés par notre middleware
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]