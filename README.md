# Agrégateur Météo Multi-Sources

> API REST FastAPI qui interroge **OpenWeather**, **Open-Meteo** et **WeatherAPI** simultanément,
> fusionne leurs réponses et les sert depuis un cache Redis intelligent à deux niveaux.
> Robuste aux pannes : circuit breaker par fournisseur, dégradation gracieuse, pré-chauffe automatique.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green)
![Tests](https://img.shields.io/badge/tests-37%20passed-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-84%25-brightgreen)
![Redis](https://img.shields.io/badge/Redis-7--alpine-red)

---

## Démarrage rapide

```bash
git clone https://github.com/kaltchagba/projet_05_meteo.git
cd projet_05_meteo
cp .env.example .env          # renseigner OPENWEATHER_API_KEY et WEATHERAPI_KEY
docker compose up -d          # démarre Redis + FastAPI + Prometheus + Grafana
curl "http://localhost:8000/meteo?ville=Paris&pays=FR"
```

---

## Sommaire

- [Architecture](#architecture)
- [Prérequis](#prérequis)
- [Lancement avec Docker](#lancement-avec-docker)
- [Endpoints de l'API](#endpoints-de-lapi)
- [Tests](#tests)
- [Linting](#linting)
- [Monitoring](#monitoring)
- [Structure du projet](#structure-du-projet)
- [Variables d'environnement](#variables-denvironnement)
- [Dépannage](#dépannage)

---

## Architecture

### Flux d'une requête `GET /meteo`

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Client HTTP                                    │
│              (curl, navigateur, application tierce)                  │
└──────────────────────────────┬───────────────────────────────────────┘
                               │  GET /meteo?ville=Paris&pays=FR
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    FastAPI — router_meteo.py                         │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  1. Validation Pydantic + normalisation (strip, upper)      │    │
│  │     + incrément compteur popularité Redis                   │    │
│  └─────────────────────────────┬───────────────────────────────┘    │
│                                │                                     │
│  ┌─────────────────────────────▼───────────────────────────────┐    │
│  │  2. Cache L2 Redis (TTL 1 h) — réponse consolidée           │    │
│  │     HIT ──────────────────────────────────────► réponse     │    │
│  │     MISS ──────────────────────────────────────► étape 3    │    │
│  └─────────────────────────────┬───────────────────────────────┘    │
│                                │                                     │
│  ┌─────────────────────────────▼───────────────────────────────┐    │
│  │  3. Appels PARALLÈLES — asyncio.gather()                    │    │
│  │                                                             │    │
│  │   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐       │    │
│  │   │ OpenWeather │  │ Open-Meteo  │  │ WeatherAPI  │       │    │
│  │   │  Cache L1   │  │  Cache L1   │  │  Cache L1   │       │    │
│  │   │  (TTL 5min) │  │  (TTL 5min) │  │  (TTL 5min) │       │    │
│  │   │  Circuit CB │  │  Circuit CB │  │  Circuit CB │       │    │
│  │   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘       │    │
│  │          └────────────────┴────────────────┘               │    │
│  └─────────────────────────────┬───────────────────────────────┘    │
│                                │                                     │
│  ┌─────────────────────────────▼───────────────────────────────┐    │
│  │  4. Fusion des résultats disponibles                        │    │
│  │     Température / Humidité / Vent  → moyenne arithmétique   │    │
│  │     Description météo              → vote majoritaire       │    │
│  │     indice_confiance               → 100 − écart-type × 20 │    │
│  │     0 provider OK                  → HTTP 503               │    │
│  └─────────────────────────────┬───────────────────────────────┘    │
│                                │                                     │
│  ┌─────────────────────────────▼───────────────────────────────┐    │
│  │  5. Écriture cache L2 (TTL 1 h) + retour réponse JSON       │    │
│  └─────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         Redis                                        │
│  Clés L1 : meteo:brut:{fournisseur}:{ville}:{pays}  (TTL 5 min)    │
│  Clés L2 : meteo:consolidee:{ville}:{pays}          (TTL 1 h)      │
│  Compteurs : meteo:compteur:{ville}:{pays}           (popularité)   │
│  Métriques : meteo:stats:hits / meteo:stats:misses                  │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│  APScheduler (thread séparé)                                        │
│  Toutes les 10 min : pré-chauffe des 20 villes les plus consultées  │
│  → renouvelle le cache avant expiration, latence ~5 ms garantie     │
└──────────────────────────────────────────────────────────────────────┘
```

### Circuit breaker — états et transitions

```
            3 erreurs / 60 s
  CLOSED ─────────────────────► OPEN
    ▲                             │
    │  succès                     │  30 secondes
    │◄────────── HALF_OPEN ◄──────┘
                   │
                   │ erreur
                   ▼
                 OPEN
```

| État | Comportement | Transition suivante |
|------|-------------|---------------------|
| `CLOSED` | Toutes les requêtes passent normalement | → OPEN si 3 erreurs en 60 s |
| `OPEN` | Requêtes bloquées, fournisseur ignoré | → HALF_OPEN après 30 s |
| `HALF_OPEN` | Une requête sonde est envoyée | → CLOSED si succès, → OPEN si échec |

### Performances mesurées

| Scénario | Latence typique |
|----------|-----------------|
| Cache L2 hit | < 10 ms |
| Cache L1 hit (tous providers) | < 50 ms |
| Appels HTTP parallèles (3 providers) | 200–700 ms |
| Objectif p95 du cahier des charges | < 800 ms |

---

## Prérequis

- **Python 3.11 ou supérieur**
- **Redis 7+** — en local ou via Docker Compose (inclus dans `docker-compose.yml`)
- **Clés API gratuites** :
  - [OpenWeatherMap](https://openweathermap.org/api) — inscription, plan gratuit suffisant
  - [WeatherAPI](https://www.weatherapi.com/) — inscription, plan gratuit suffisant
  - **Open-Meteo** — aucune clé requise, 100 % public

---

## Lancement avec Docker

Le fichier `docker-compose.yml` orchestre **4 services** :

| Service | Port | Description |
|---------|------|-------------|
| `app` | 8000 | FastAPI via Uvicorn |
| `redis` | 6379 | Redis 7 Alpine (healthcheck `redis-cli ping`) |
| `prometheus` | 9090 | Collecte des métriques `/metrics` toutes les 15 s |
| `grafana` | 3000 | Tableaux de bord (login `admin` / `admin`) |

```bash
# Première utilisation
cp .env.example .env
# Renseigner OPENWEATHER_API_KEY et WEATHERAPI_KEY dans .env

# Démarrer tous les services
docker compose up -d

# Reconstruire l'image (après modification de requirements.txt ou Dockerfile)
docker compose up --build -d

# Suivre les logs en temps réel
docker compose logs -f app

# Arrêter
docker compose down

# Arrêter et effacer les volumes Redis (cache vide au redémarrage)
docker compose down -v

# Redémarrer uniquement l'application
docker compose restart app
```

> L'application démarre uniquement quand Redis est sain (`condition: service_healthy`).

---

## Endpoints de l'API

### Récapitulatif

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| `GET` | `/meteo` | Données fusionnées des 3 fournisseurs |
| `GET` | `/comparer` | Données brutes côte à côte + écarts inter-sources |
| `GET` | `/sante` | État des circuit breakers et du cache |
| `GET` | `/dashboard` | Dashboard HTML (métriques, auto-refresh 10 s) |
| `GET` | `/interface` | Interface météo interactive (recherche, dark/light mode) |
| `WS` | `/ws/meteo/{ville}` | Flux météo en temps réel (WebSocket, toutes les 30 s) |
| `GET` | `/docs` | Swagger UI interactif |
| `GET` | `/redoc` | Documentation ReDoc |
| `GET` | `/metrics` | Métriques Prometheus |

---

### `GET /meteo` — Données consolidées

```bash
# Avec code pays (recommandé)
curl "http://localhost:8000/meteo?ville=Paris&pays=FR"
curl "http://localhost:8000/meteo?ville=Lomé&pays=TG"
curl "http://localhost:8000/meteo?ville=New%20York&pays=US"
curl "http://localhost:8000/meteo?ville=Tokyo&pays=JP"

# Affichage formaté
curl -s "http://localhost:8000/meteo?ville=Paris&pays=FR" | python -m json.tool
```

**Paramètres :**

| Paramètre | Type | Obligatoire | Défaut | Contraintes |
|-----------|------|-------------|--------|-------------|
| `ville` | string | Oui | — | 1 à 100 caractères |
| `pays` | string | Non | `FR` | Exactement 2 caractères (ISO 3166-1 alpha-2) |

**Réponse HTTP 200 — 3 fournisseurs OK :**

```json
{
  "ville": "Paris",
  "pays": "FR",
  "temperature_c": 18.3,
  "humidite_pct": 72.0,
  "vent_kmh": 14.7,
  "description": "ciel dégagé",
  "fournisseurs_ok": ["openweather", "open_meteo", "weatherapi"],
  "fournisseurs_ko": [],
  "nb_sources": 3,
  "depuis_cache": false,
  "genere_a": "2026-06-24T12:00:00+00:00",
  "indice_confiance": 94,
  "avertissement": null
}
```

**Réponse HTTP 200 — panne partielle (1 fournisseur KO) :**

```json
{
  "ville": "Lyon",
  "pays": "FR",
  "temperature_c": 21.5,
  "humidite_pct": 65.0,
  "vent_kmh": 11.0,
  "description": "partiellement nuageux",
  "fournisseurs_ok": ["openweather", "open_meteo"],
  "fournisseurs_ko": ["weatherapi"],
  "nb_sources": 2,
  "depuis_cache": false,
  "genere_a": "2026-06-24T12:00:00+00:00",
  "indice_confiance": 87,
  "avertissement": null
}
```

**Réponse HTTP 200 — depuis le cache Redis L2 :**

```json
{
  "ville": "Paris",
  "pays": "FR",
  "temperature_c": 18.3,
  "humidite_pct": 72.0,
  "vent_kmh": 14.7,
  "description": "ciel dégagé",
  "fournisseurs_ok": ["openweather", "open_meteo", "weatherapi"],
  "fournisseurs_ko": [],
  "nb_sources": 3,
  "depuis_cache": true,
  "genere_a": "2026-06-24T12:00:00+00:00",
  "indice_confiance": 94,
  "avertissement": null
}
```

**Réponse HTTP 200 — données suspectes (code pays incorrect ou ville ambiguë) :**

```json
{
  "ville": "Paris",
  "pays": "JP",
  "temperature_c": 28.1,
  "humidite_pct": 80.0,
  "vent_kmh": 12.3,
  "description": "pluie légère",
  "fournisseurs_ok": ["openweather", "open_meteo", "weatherapi"],
  "fournisseurs_ko": [],
  "nb_sources": 3,
  "depuis_cache": false,
  "genere_a": "2026-06-24T12:00:00+00:00",
  "indice_confiance": 12,
  "avertissement": "Divergence importante entre fournisseurs (11.4°C d'écart). Vérifiez le code pays « JP » — il est peut-être incorrect pour Paris."
}
```

**Réponse HTTP 503 — panne totale :**

```json
{
  "detail": {
    "erreur": "Aucun fournisseur météo disponible",
    "fournisseurs_ko": ["openweather", "open_meteo", "weatherapi"],
    "suggestion": "Réessayez dans quelques secondes."
  }
}
```

**Description des champs de la réponse :**

| Champ | Type | Description |
|-------|------|-------------|
| `ville` | string | Nom de la ville transmis |
| `pays` | string | Code pays en majuscules |
| `temperature_c` | float | Moyenne des températures (°C) |
| `humidite_pct` | float | Moyenne des humidités relatives (%) |
| `vent_kmh` | float | Moyenne des vitesses de vent (km/h) |
| `description` | string | Condition météo par vote majoritaire |
| `fournisseurs_ok` | array | Fournisseurs ayant répondu avec succès |
| `fournisseurs_ko` | array | Fournisseurs en erreur ou circuit ouvert |
| `nb_sources` | integer | Nombre de fournisseurs contribuant à la fusion (≥ 1) |
| `depuis_cache` | boolean | `true` si servi depuis le cache Redis L2 |
| `genere_a` | datetime | Horodatage UTC de la réponse |
| `indice_confiance` | integer | Accord inter-sources 0–100 (formule : `max(0, min(100, 100 − écart_type × 20))`) |
| `avertissement` | string\|null | Alerte si divergence ≥ 8 °C ou seulement 1 source disponible sur 3 |

---

### `GET /comparer` — Comparaison des sources brutes

Retourne les données non fusionnées de chaque fournisseur côte à côte, les écarts maximaux par métrique et l'indice de consensus.

```bash
curl -s "http://localhost:8000/comparer?ville=Paris&pays=FR" | python -m json.tool
```

**Réponse HTTP 200 :**

```json
{
  "ville": "Paris",
  "pays": "FR",
  "sources": {
    "openweather": {
      "temperature_c": 18.1,
      "humidite_pct": 71.0,
      "vent_kmh": 14.0,
      "description": "ciel dégagé",
      "depuis_cache": false
    },
    "open_meteo": {
      "temperature_c": 18.5,
      "humidite_pct": 73.0,
      "vent_kmh": 15.2,
      "description": "ciel dégagé",
      "depuis_cache": true
    },
    "weatherapi": {
      "temperature_c": 18.4,
      "humidite_pct": 72.0,
      "vent_kmh": 14.9,
      "description": "partiellement nuageux",
      "depuis_cache": false
    }
  },
  "ecarts": {
    "temperature_c": 0.4,
    "humidite_pct": 2.0,
    "vent_kmh": 1.2
  },
  "indice_consensus": 94,
  "fournisseurs_ok": ["openweather", "open_meteo", "weatherapi"],
  "fournisseurs_ko": [],
  "genere_a": "2026-06-24T12:00:00+00:00"
}
```

---

### `GET /sante` — État de santé

```bash
curl -s "http://localhost:8000/sante" | python -m json.tool
```

**Réponse HTTP 200 :**

```json
{
  "status": "dégradé",
  "fournisseurs": [
    {
      "fournisseur": "openweather",
      "etat": "CLOSED",
      "nb_erreurs": 0,
      "dernier_succes": "2026-06-24T11:42:17+00:00"
    },
    {
      "fournisseur": "open_meteo",
      "etat": "CLOSED",
      "nb_erreurs": 0,
      "dernier_succes": "2026-06-24T11:42:17+00:00"
    },
    {
      "fournisseur": "weatherapi",
      "etat": "OPEN",
      "nb_erreurs": 3,
      "dernier_succes": null
    }
  ],
  "cache_hits": 47,
  "cache_misses": 12,
  "verifie_a": "2026-06-24T11:45:02+00:00"
}
```

| Valeur `status` | Signification |
|-----------------|---------------|
| `ok` | Les 3 circuit breakers sont CLOSED |
| `dégradé` | 1 ou 2 circuit breakers sont OPEN |
| `critique` | Les 3 circuit breakers sont OPEN |

---

### `GET /interface` — Interface météo interactive

Ouvrir dans un navigateur : `http://localhost:8000/interface`

Interface HTML complète avec :
- Champ de recherche ville + code pays ISO
- Affichage de la météo fusionnée avec fond dynamique selon les conditions météo
- Barre d'indice de confiance inter-sources avec label textuel
- Pills de statut par fournisseur (vert / rouge)
- Panneau de comparaison des sources brutes (détail par fournisseur)
- Flux WebSocket live (rafraîchissement toutes les 30 s)
- Toggle mode clair / sombre (persisté en `localStorage`)

---

### `GET /dashboard` — Dashboard de monitoring

Ouvrir dans un navigateur : `http://localhost:8000/dashboard`

Page HTML auto-rafraîchissante toutes les 10 secondes. Affiche les compteurs de hits et misses Redis, le ratio en pourcentage, et l'état en temps réel des 3 circuit breakers avec code couleur.

---

### `WS /ws/meteo/{ville}` — Flux en temps réel

```javascript
// Connexion WebSocket depuis le navigateur ou un client
const ws = new WebSocket('ws://localhost:8000/ws/meteo/Paris?pays=FR&interval=30');
ws.onmessage = (e) => console.log(JSON.parse(e.data));
```

Paramètres query string :

| Paramètre | Défaut | Contrainte |
|-----------|--------|------------|
| `pays` | `FR` | 2 caractères ISO |
| `interval` | `30` | Clampé entre 10 s et 60 s |

Le serveur pousse un objet `MeteoResponse` JSON complet à chaque intervalle.
En cas d'erreur ponctuelle, il envoie `{"erreur": "message"}` sans couper la connexion.

---

## Tests

### Lancer la suite complète

```bash
pytest
```

37 tests, durée habituelle 60–90 secondes. Aucune dépendance externe :
- Redis simulé par `fakeredis.FakeRedis(decode_responses=True)`
- Appels HTTP aux fournisseurs interceptés par `respx`

### Avec couverture de code

```bash
pytest --cov=app --cov-report=term-missing
```

Résultats mesurés (Python 3.13, pytest 8.3.3) :

```
Name                              Stmts   Miss  Cover
-----------------------------------------------------
app/__init__.py                       0      0   100%
app/config.py                        17      0   100%
app/metrics.py                        8      0   100%
app/providers/__init__.py             0      0   100%
app/providers/openweather.py         16      0   100%
app/providers/weatherapi.py          19      0   100%
app/providers/open_meteo.py          36      1    97%
app/schemas.py                       70      3    96%
app/scheduler.py                     70      6    91%
app/main.py                          67     12    82%
app/cache.py                        148     29    80%
app/circuit_breaker.py               80     16    80%
app/router_meteo.py                 167     44    74%
-----------------------------------------------------
TOTAL                               698    111    84%
```

Couverture actuelle : **84 %** — seuil minimum imposé : 70 %.

### Lancer un test précis

```bash
# Un seul test par nom
pytest tests/test_meteo.py::test_succes_complet -v

# Tous les tests d'un module
pytest tests/test_meteo.py -v
pytest tests/test_cache.py -v
pytest tests/test_scheduler.py -v

# Les 10 tests les plus lents
pytest --durations=10
```

### Scénarios couverts

| Scénario | Fichier |
|----------|---------|
| Succès complet — 3 fournisseurs | `test_meteo.py` |
| Panne d'un fournisseur (2 répondent) | `test_meteo.py` |
| Panne de deux fournisseurs (1 répond) | `test_meteo.py` |
| Panne totale → HTTP 503 | `test_meteo.py` |
| Réponses très divergentes entre fournisseurs | `test_meteo.py` |
| Circuit breaker OPEN après 3 erreurs | `test_meteo.py` |
| Cache L2 hit → zéro appel HTTP | `test_meteo.py` |
| `GET /sante` — statut et état circuit breakers | `test_meteo.py` |
| `GET /comparer` — sources brutes + écarts | `test_meteo.py` |
| Cache L1 : écriture, lecture, TTL, normalisation clés | `test_cache.py` |
| Cache L2 : écriture, lecture, TTL | `test_cache.py` |
| Compteurs de popularité Redis | `test_cache.py` |
| Métriques hit/miss et ratio (division par zéro inclus) | `test_cache.py` |
| Dégradation gracieuse Redis indisponible | `test_cache.py` |
| Pré-chauffe : cache encore valide → aucun appel HTTP | `test_scheduler.py` |
| Pré-chauffe : 3 providers OK → écriture cache L2 | `test_scheduler.py` |
| Pré-chauffe : tous KO → pas d'exception levée | `test_scheduler.py` |
| Villes par défaut si Redis vide | `test_scheduler.py` |
| Villes populaires récupérées depuis Redis | `test_scheduler.py` |
| Erreur sur une ville n'arrête pas les autres | `test_scheduler.py` |
| Cycle de vie scheduler : démarrer et arrêter | `test_scheduler.py` |

---

## Linting

```bash
# Vérifier
ruff check .

# Corriger automatiquement
ruff check . --fix

# Formater
ruff format .
```

---

## Monitoring

### Prometheus

Métriques exposées sur `http://localhost:8000/metrics` (format texte Prometheus) :

| Métrique | Type | Description |
|----------|------|-------------|
| `meteo_appels_fournisseur_total` | Counter | Appels par fournisseur et statut (succès / erreur) |
| `meteo_latence_fournisseur_seconds` | Histogram | Latence HTTP par fournisseur |
| `meteo_etat_circuit_breaker` | Gauge | État CB : 0 = CLOSED, 1 = OPEN, 2 = HALF_OPEN |
| `meteo_operations_cache_total` | Counter | Hits et misses Redis |

Prometheus scrape l'endpoint toutes les 15 secondes (configuré dans `monitoring/prometheus.yml`).

### Grafana

Disponible sur `http://localhost:3000` (login `admin` / `admin`).

Un dashboard **"Agrégateur Météo"** est provisionné automatiquement au démarrage.
Il affiche : latence p95 par fournisseur, taux de hit cache, état des circuit breakers en temps réel.

---

## Structure du projet

```
projet_05_meteo/
│
├── app/
│   ├── __init__.py
│   ├── main.py                    # Lifespan FastAPI, /sante, /dashboard, /interface
│   ├── config.py                  # Pydantic Settings — lit le fichier .env
│   ├── schemas.py                 # Modèles Pydantic v2 (entrée, interne, sortie, /comparer)
│   ├── router_meteo.py            # GET /meteo, GET /comparer, WS /ws/meteo/{ville}
│   ├── cache.py                   # Redis L1/L2, compteurs popularité, métriques hit/miss
│   ├── circuit_breaker.py         # Classe CircuitBreaker + 3 instances globales
│   ├── metrics.py                 # Compteurs et histogrammes Prometheus
│   ├── scheduler.py               # APScheduler, pré-chauffe 20 villes / 10 min
│   └── providers/
│       ├── __init__.py
│       ├── openweather.py         # Client API OpenWeatherMap v2.5
│       ├── open_meteo.py          # Client API Open-Meteo (géocodage + météo)
│       └── weatherapi.py          # Client API WeatherAPI current.json
│
├── tests/
│   ├── conftest.py                # Fixtures : TestClient, fakeredis, reset circuit breakers
│   ├── test_meteo.py              # 9 tests bout en bout des routes
│   ├── test_cache.py              # 21 tests unitaires cache Redis
│   └── test_scheduler.py          # 7 tests unitaires scheduler
│
├── monitoring/
│   ├── prometheus.yml             # Scrape config (interval 15 s)
│   └── grafana/
│       ├── provisioning/          # Datasource et dashboard auto-provisionnés
│       └── dashboards/
│           └── meteo.json         # Dashboard Grafana "Agrégateur Météo"
│
├── .github/
│   └── workflows/
│       └── ci.yml                 # CI : lint (ruff) → tests (--cov-fail-under=70) → build Docker
│
├── Dockerfile                     # Image Python 3.11-slim
├── docker-compose.yml             # 4 services : app + redis + prometheus + grafana
├── requirements.txt               # Dépendances Python versionnées
├── pytest.ini                     # asyncio_mode=auto, testpaths=tests
├── .env.example                   # Modèle de configuration (sans secrets, commité)
├── .env                           # Configuration réelle (ignoré par .gitignore)
└── .gitignore
```

---

## Variables d'environnement

Copier `.env.example` en `.env` et ajuster les valeurs.

| Variable | Défaut | Description |
|----------|--------|-------------|
| `OPENWEATHER_API_KEY` | *(vide)* | Clé OpenWeatherMap ([gratuit](https://openweathermap.org/api)) |
| `WEATHERAPI_KEY` | *(vide)* | Clé WeatherAPI ([gratuit](https://www.weatherapi.com/)) |
| `REDIS_URL` | `redis://localhost:6379` | `redis://redis:6379` avec Docker Compose |
| `CACHE_COURT_TTL` | `300` | TTL cache L1 en secondes (5 min) |
| `CACHE_LONG_TTL` | `3600` | TTL cache L2 en secondes (1 h) |
| `PROVIDER_TIMEOUT` | `5.0` | Timeout HTTP par fournisseur (secondes) |
| `CIRCUIT_BREAKER_THRESHOLD` | `3` | Erreurs avant ouverture du circuit |
| `CIRCUIT_BREAKER_RECOVERY` | `30` | Secondes avant OPEN → HALF_OPEN |
| `CIRCUIT_BREAKER_WINDOW` | `60` | Fenêtre glissante de comptage des erreurs (secondes) |
| `TOP_CITIES_COUNT` | `20` | Villes populaires à pré-chauffer |
| `SCHEDULER_INTERVAL_MINUTES` | `10` | Intervalle entre deux sessions de pré-chauffe (minutes) |

---

## Dépannage

**`ConnectionRefusedError` au démarrage**

Redis n'est pas joignable sur l'URL configurée dans `.env` :

```bash
redis-cli ping          # doit répondre PONG
# ou
docker run -d --name redis-meteo -p 6379:6379 redis:7-alpine
```

---

**HTTP 503 sur tous les appels `/meteo`**

Les 3 circuit breakers sont OPEN. Causes fréquentes :
- Clés API absentes ou invalides dans `.env`
- Absence de connexion Internet
- Rate limit des APIs gratuites atteint

Consulter `GET /sante` puis les logs : `docker compose logs -f app`.

---

**HTTP 422 sur `/meteo`**

Contraintes Pydantic non respectées :
- `ville` est vide ou dépasse 100 caractères
- `pays` ne fait pas exactement 2 caractères

Exemple valide : `?ville=Paris&pays=FR`

---

**`avertissement` présent dans la réponse**

L'API a détecté une incohérence :
- **Divergence ≥ 8 °C** entre fournisseurs → code pays probablement incorrect
- **1 seule source** sur 3 a répondu → ville introuvable ou mal orthographiée

Vérifier le code pays (ISO 3166-1 alpha-2) et le nom de la ville.

---

**Cache Redis périmé après modification de la configuration**

Supprimer manuellement les clés L2 concernées :

```bash
# Une ville précise
docker exec meteo_redis redis-cli DEL "meteo:consolidee:paris:fr"

# Vider tout le cache
docker exec meteo_redis redis-cli FLUSHDB
```

---

**Couverture de tests insuffisante**

Vérifier que `fakeredis` est installé :

```bash
pip install fakeredis==2.26.1
```

Les tests ne nécessitent pas de vraies clés API. Les variables d'environnement peuvent
rester vides ou fictives — `fakeredis` et `respx` interceptent tout.
