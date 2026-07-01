#!/bin/bash
set -e

# Générer .env depuis .env.example si absent
if [ ! -f .env ]; then
    cp .env.example .env
fi

# Injecter les secrets Codespaces s'ils sont définis
if [ -n "$OPENWEATHER_API_KEY" ]; then
    sed -i "s|OPENWEATHER_API_KEY=.*|OPENWEATHER_API_KEY=$OPENWEATHER_API_KEY|" .env
fi
if [ -n "$WEATHERAPI_KEY" ]; then
    sed -i "s|WEATHERAPI_KEY=.*|WEATHERAPI_KEY=$WEATHERAPI_KEY|" .env
fi

# Démarrage initial
docker compose up -d --build
