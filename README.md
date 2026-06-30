# Projet 05 — Agrégateur météo multi-sources (MiabeMETEO)

Python 3.11
FastAPI
Redis
Docker
pytest

API météo qui interroge trois fournisseurs indépendants (OpenWeatherMap, Open-Meteo, WeatherAPI) en parallèle, fusionne leurs données et les sert avec un cache Redis à deux niveaux et un circuit breaker par fournisseur.

**Équipe** : Groupe 5 — SONHOUIN Abdoul-Raouf · TCHAGBA Kaled · YEYE Koffi Gagnon  
**Cours** : APIs Web Flask & FastAPI — ESGIS M1 IA & Big Data 2025-2026  
**Enseignant** : TCHAYE-KONDI Jude, Ph.D.

---

## Conformité au cahier des charges


| Exigence Projet 05                                                         | Implémentation                                                    |
| -------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `GET /meteo` — 3 sources en parallèle, fusion moyenne + vote, p95 < 800 ms | `router_meteo.py` — `asyncio.gather()` + cache L2 < 10 ms         |
| Circuit breaker OPEN / HALF_OPEN / CLOSED, fenêtre 60 s                    | `circuit_breaker.py` — 3 instances indépendantes, seuil 3 erreurs |
| Cache Redis L1 (5 min / fournisseur) + L2 (1 h consolidé)                  | `cache.py` — clés `meteo:brut:…` et `meteo:consolidee:…`          |
| Scheduler : pré-chauffe 20 villes toutes les 10 min                        | `scheduler.py` — APScheduler BackgroundScheduler                  |
| `GET /sante` — état détaillé de chaque source                              | `main.py` — `EtatCircuitBreaker` + hits/misses Redis              |
| Dashboard HTML métriques hit/miss                                          | `/dashboard` — HTML généré, auto-refresh 10 s                     |
| Stack : FastAPI, httpx, Redis, APScheduler, pytest, respx                  | `requirements.txt`                                                |
| Tests sans appel réel (respx + fakeredis), couverture ≥ 70 %               | 58 tests, 82 % de couverture — mesuré le 01/07/2026               |
| Logs JSON structurés                                                       | `pythonjsonlogger` configuré dans `main.py`                       |
| Secrets dans `.env`, jamais dans le dépôt                                  | `.env.example` fourni, `.env` dans `.gitignore`                   |
| Dockerfile + docker-compose.yml                                            | Multi-stage build, 4 services                                     |
| Pipeline CI (lint + tests + build Docker)                                  | `.github/workflows/ci.yml` — 3 jobs GitHub Actions                |
| OpenAPI / Swagger                                                          | `/docs` (Swagger UI) et `/redoc` (ReDoc)                          |


---

## Fonctionnalités complémentaires

Ces fonctionnalités dépassent le cahier des charges et constituent la part d'originalité du projet :

- `GET /comparer` — données brutes de chaque fournisseur côte à côte avec écarts et indice de consensus
- `GET /previsions` — prévisions journalières sur 7 jours via Open-Meteo
- `GET /historique` — fenêtre glissante de 48 relevés par ville (Redis List)
- `GET /alertes` — alertes automatiques canicule (> 35 °C), gel (< 0 °C), vent fort (> 80 km/h)
- `GET /villes-populaires` — classement Redis Sorted Set des villes les plus consultées
- `GET /export` — export CSV ou JSON téléchargeable avec historique embarqué
- `POST /batch` — météo de 1 à 10 villes en une seule requête (`asyncio.gather`)
- `WS /ws/stats` — métriques système poussées toutes les 5 s
- `WS /ws/meteo/{ville}` — flux météo temps réel, intervalle 10–60 s
- `/interface` — interface web 5 onglets avec carte Leaflet, géolocalisation, dark/light mode
- Prometheus + Grafana autoprovisionné dans le docker-compose

---

## Architecture

```
Navigateur / Client HTTP
        │
        ▼
┌───────────────────────────────────────────────────┐
│              FastAPI · uvicorn                    │
│                                                   │
│  Middleware  ──► CSP nonce · Rate limit · Headers │
│                                                   │
│  GET /meteo                                       │
│    │                                              │
│    ├─ Cache L2 (1h) HIT ?  ──► réponse directe   │
│    │                                              │
│    ├─ Cache L1 (5min) partiel ?  ──► fusion       │
│    │                                              │
│    └─ asyncio.gather(                             │
│         openweather.fetch()  ◄── Circuit Breaker  │
│         open_meteo.fetch()   ◄── Circuit Breaker  │
│         weatherapi.fetch()   ◄── Circuit Breaker  │
│       )                                           │
│         │                                        │
│         └─► Fusion (moyenne + vote description)  │
│              │                                   │
│              └─► Redis L1 + L2 · Alertes ·       │
│                  Historique · Classement          │
└───────────────────────────────────────────────────┘
        │
        ▼
     Redis 7 (Docker)
        │
        ▼
  Prometheus ──► Grafana
```


| Module               | Rôle                                                                    |
| -------------------- | ----------------------------------------------------------------------- |
| `main.py`            | Lifespan FastAPI, routes `/sante`, `/dashboard`, `/interface`           |
| `router_meteo.py`    | `GET /meteo`, `GET /comparer`, `WS /ws/meteo/{ville}`                   |
| `router_donnees.py`  | Historique, alertes, classement, prévisions, export, batch, `/ws/stats` |
| `cache.py`           | Redis L1/L2, compteurs popularité, métriques hit/miss, alertes          |
| `circuit_breaker.py` | 3 instances globales, transitions CLOSED/OPEN/HALF_OPEN                 |
| `scheduler.py`       | APScheduler : pré-chauffe 20 villes toutes les 10 min                   |
| `providers/`         | `openweather.py`, `open_meteo.py`, `weatherapi.py` — appels httpx async |
| `schemas.py`         | Modèles Pydantic v2 : `MeteoResponse`, `ComparaisonResponse`, etc.      |
| `metrics.py`         | Compteurs et jauges Prometheus                                          |
| `middleware.py`      | Rate limiting par IP + 7 headers de sécurité HTTP                       |
| `config.py`          | Lecture `.env` via variables d'environnement                            |


---

## Installation et lancement

### Prérequis

- **Docker + Docker Compose** (recommandé)
- Clés API gratuites : [OpenWeatherMap](https://openweathermap.org/api) · [WeatherAPI](https://www.weatherapi.com/)
- Open-Meteo ne nécessite aucune clé

### Lancement avec Docker (recommandé)

```bash
cd projet_05_meteo
cp .env.example .env
# Éditer .env : renseigner OPENWEATHER_API_KEY et WEATHERAPI_KEY
docker compose up --build
```

URLs disponibles après démarrage :


| Service         | URL                                                                    |
| --------------- | ---------------------------------------------------------------------- |
| API / Swagger   | [http://localhost:8000/docs](http://localhost:8000/docs)               |
| Interface météo | [http://localhost:8000/interface](http://localhost:8000/interface)     |
| Dashboard       | [http://localhost:8000/dashboard](http://localhost:8000/dashboard)     |
| Santé           | [http://localhost:8000/sante](http://localhost:8000/sante)             |
| Prometheus      | [http://localhost:9090](http://localhost:9090)                         |
| Grafana         | [http://localhost:3000](http://localhost:3000) — `admin` / `meteo2026` |


### Variante développement (sans Docker)

```bash
cd projet_05_meteo
python -m venv .venv && source .venv/bin/activate  # ou .venv\Scripts\activate sur Windows
pip install -r requirements.txt

# Redis dans un terminal séparé
docker run -d -p 6379:6379 redis:7-alpine

cp .env.example .env  # renseigner les clés
uvicorn app.main:app --reload --port 8000
```

Variables essentielles dans `.env` :

```env
OPENWEATHER_API_KEY=votre_cle_ici
WEATHERAPI_KEY=votre_cle_ici
REDIS_URL=redis://localhost:6379/0
CACHE_COURT_TTL=300    # 5 min — L1 par fournisseur
CACHE_LONG_TTL=3600    # 1 h   — L2 consolidé
```

---

## Exemples d'appels API

```bash
# Météo actuelle — 3 sources fusionnées
curl "http://localhost:8000/meteo?ville=Paris&pays=FR"

# Même requête une seconde fois — observe depuis_cache: true
curl "http://localhost:8000/meteo?ville=Paris&pays=FR"

# Données brutes côte à côte avec écarts
curl "http://localhost:8000/comparer?ville=Lomé&pays=TG"

# État de l'application (circuit breakers + cache)
curl "http://localhost:8000/sante"

# Validation : ville absente → 422 Unprocessable Entity
curl "http://localhost:8000/meteo?pays=FR"

# Export JSON téléchargeable avec historique
curl -O "http://localhost:8000/export?ville=Paris&pays=FR&format=json"
```

---

## Interface web

L'interface `/interface` présente 5 onglets utilisables en soutenance :

- **Données** — résultat fusionné, indice de confiance, pastilles fournisseurs, fond dynamique selon conditions
- **Sources / Comparer** — données brutes par fournisseur, écarts, indice de consensus
- **Prévisions** — 7 jours via Open-Meteo, températures min/max, précipitations
- **Historique** — courbe de la fenêtre glissante des 48 derniers relevés
- **Carte** — Leaflet.js avec tuiles CartoDB, clic n'importe où pour la météo locale, géolocalisation

---

## Tests et qualité

```bash
# Suite complète (58 tests, sans Redis ni clés API réelles)
pytest

# Avec rapport de couverture
pytest --cov=app --cov-report=term-missing

# Test ciblé
pytest tests/test_meteo.py::test_succes_complet -v

# Seuil CI : coverage < 70 % → pipeline en erreur
pytest --cov=app --cov-fail-under=70
```

Couverture mesurée le 01/07/2026 : **82 %** (seuil requis 70 %).


| Fichier             | Tests | Scénarios principaux                                                              |
| ------------------- | ----- | --------------------------------------------------------------------------------- |
| `test_meteo.py`     | 9     | 3 OK, 2 OK, 1 OK, 0 OK → 503, incohérences, circuit breaker, /sante, validation   |
| `test_cache.py`     | 21    | L1/L2 lecture/écriture, TTL, normalisation clés, popularité, Redis indisponible   |
| `test_extras.py`    | 21    | Historique, alertes, classement, export, batch, prévisions, interface, rate limit |
| `test_scheduler.py` | 7     | Cache valide, pré-chauffe, panne totale, villes défaut/populaires, cycle de vie   |


Aucun appel réseau réel : Redis simulé par `fakeredis`, HTTP intercepté par `respx`.

```bash
# Lint
ruff check .
ruff check . --fix
```

---

## CI/CD

Pipeline GitHub Actions déclenché sur push/PR vers `main`, `develop` et `kaled` :


| Job        | Commande                               | Condition   |
| ---------- | -------------------------------------- | ----------- |
| **lint**   | `ruff check .`                         | toujours    |
| **tests**  | `pytest --cov=app --cov-fail-under=70` | si lint OK  |
| **docker** | `docker build -t meteo-app:ci .`       | si tests OK |


Voir [.github/workflows/ci.yml](.github/workflows/ci.yml).

---

## Monitoring (docker-compose complet)


| Tableau de bord      | URL                                                                |
| -------------------- | ------------------------------------------------------------------ |
| Dashboard interne    | [http://localhost:8000/dashboard](http://localhost:8000/dashboard) |
| Santé JSON           | [http://localhost:8000/sante](http://localhost:8000/sante)         |
| Métriques Prometheus | [http://localhost:8000/metrics](http://localhost:8000/metrics)     |
| Prometheus           | [http://localhost:9090](http://localhost:9090)                     |
| Grafana              | [http://localhost:3000](http://localhost:3000)                     |


---

## Structure du dépôt

```
projet_05_meteo/
├── app/
│   ├── main.py              # Point d'entrée, cycle de vie, routes HTML
│   ├── config.py            # Constantes depuis .env
│   ├── schemas.py           # Modèles Pydantic v2
│   ├── cache.py             # Cache Redis 2 niveaux + historique + alertes
│   ├── circuit_breaker.py   # Circuit Breaker thread-safe
│   ├── middleware.py        # Rate limiting + headers sécurité HTTP
│   ├── metrics.py           # Compteurs Prometheus
│   ├── scheduler.py         # Pré-chauffe APScheduler
│   ├── router_meteo.py      # Route /meteo — orchestration principale
│   ├── router_donnees.py    # Routes secondaires
│   ├── providers/
│   │   ├── openweather.py
│   │   ├── open_meteo.py
│   │   └── weatherapi.py
│   └── templates/
│       └── interface.html   # Interface MiabeMETEO (Leaflet.js)
├── tests/
│   ├── conftest.py
│   ├── test_meteo.py        # 9 tests — routes principales
│   ├── test_cache.py        # 21 tests — module cache
│   ├── test_extras.py       # 21 tests — endpoints complémentaires
│   └── test_scheduler.py    # 7 tests — scheduler
├── monitoring/
│   ├── prometheus.yml
│   └── grafana/
├── Dockerfile               # Multi-stage build (builder + runtime)
├── docker-compose.yml       # 4 services : app, redis, prometheus, grafana
├── requirements.txt
├── pytest.ini
├── .env.example
```

---

## Références

- [PROJETS.md](../projets/PROJETS.md) — cahier des charges et barème /20
- [OpenWeatherMap API](https://openweathermap.org/api) · [Open-Meteo](https://open-meteo.com/) · [WeatherAPI](https://www.weatherapi.com/)
- Cours : APIs Web Flask + FastAPI — TCHAYE-KONDI Jude, Ph.D. — ESGIS 2025-2026

