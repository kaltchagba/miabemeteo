# Agrégateur Météo Multi-Sources

> API REST FastAPI qui interroge **OpenWeather**, **Open-Meteo** et **WeatherAPI** simultanément,
> fusionne leurs réponses et les sert depuis un cache Redis intelligent à deux niveaux.
> Robuste aux pannes : circuit breaker par fournisseur, dégradation gracieuse, pré-chauffe automatique.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green)
![Tests](https://img.shields.io/badge/tests-37%20passed-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-87%25-brightgreen)
![Redis](https://img.shields.io/badge/Redis-7--alpine-red)

---

## Démarrage rapide

```bash
git clone https://github.com/abdoul9001/projet_05_meteo.git
cd projet_05_meteo
cp .env.example .env          # puis renseigner OPENWEATHER_API_KEY et WEATHERAPI_KEY
docker compose up             # démarre Redis + FastAPI
curl "http://localhost:8000/meteo?ville=Paris&pays=FR"
```

---

## Sommaire

- [Architecture](#architecture)
- [Prérequis](#prérequis)
- [Installation locale](#installation-locale)
- [Lancement local](#lancement-local)
- [Lancement avec Docker](#lancement-avec-docker)
- [Utilisation de l'API](#utilisation-de-lapi)
- [Tests](#tests)
- [Linting](#linting)
- [Structure du projet](#structure-du-projet)
- [Variables d'environnement](#variables-denvironnement)
- [Dépannage](#dépannage)

---

## Architecture

### Vue d'ensemble

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
│  │  1. Normalisation (strip, upper) + compteur popularité      │    │
│  └─────────────────────────────┬───────────────────────────────┘    │
│                                │                                     │
│  ┌─────────────────────────────▼───────────────────────────────┐    │
│  │  2. Cache L2 Redis (TTL 1h) — réponse consolidée            │    │
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
│  │     0 provider OK                  → HTTP 503               │    │
│  └─────────────────────────────┬───────────────────────────────┘    │
│                                │                                     │
│  ┌─────────────────────────────▼───────────────────────────────┐    │
│  │  5. Écriture cache L2 (TTL 1h) + retour réponse JSON        │    │
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
│  → renouvelle le cache avant expiration, latence ~5ms garantie      │
└──────────────────────────────────────────────────────────────────────┘
```

### Circuit breaker — états et transitions

```
            3 erreurs / 60s
  CLOSED ─────────────────────► OPEN
    ▲                             │
    │    succès                   │  30 secondes
    │◄────────── HALF_OPEN ◄──────┘
                   │
                   │ erreur
                   ▼
                 OPEN
```

| État | Comportement | Transition suivante |
|------|-------------|---------------------|
| `CLOSED` | Toutes les requêtes passent normalement | → OPEN si 3 erreurs en 60s |
| `OPEN` | Requêtes bloquées, fournisseur ignoré | → HALF_OPEN après 30s |
| `HALF_OPEN` | 1 requête sonde envoyée | → CLOSED si succès, → OPEN si échec |

### Performance attendue

| Scénario | Latence typique |
|----------|-----------------|
| Cache L2 hit | < 10 ms |
| Cache L1 hit (tous providers) | < 50 ms |
| Appels HTTP parallèles (3 providers) | 200–700 ms |
| Objectif p95 garanti par le sujet | < 800 ms |

---

## Prérequis

- **Python 3.11 ou supérieur**
- **Redis 7+** — en local ou via Docker Compose (inclus dans `docker-compose.yml`)
- **Clés API gratuites** :
  - [OpenWeatherMap](https://openweathermap.org/api) — inscription, plan gratuit suffisant
  - [WeatherAPI](https://www.weatherapi.com/) — inscription, plan gratuit suffisant
  - **Open-Meteo** — aucune clé requise, 100 % public

---

## Installation locale

### 1. Cloner le dépôt

```bash
git clone https://github.com/abdoul9001/projet_05_meteo.git
cd projet_05_meteo
```

### 2. Créer et activer l'environnement virtuel

```bash
python -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Windows (cmd)
.venv\Scripts\activate.bat
```

### 3. Installer les dépendances

```bash
pip install -r requirements.txt
```

### 4. Configurer les variables d'environnement

```bash
cp .env.example .env
```

Ouvrir `.env` et renseigner au minimum :

```env
OPENWEATHER_API_KEY=votre_cle_ici
WEATHERAPI_KEY=votre_cle_ici

# En local (sans Docker Compose) :
REDIS_URL=redis://localhost:6379

# En Docker Compose, laisser la valeur par défaut du fichier .env.example :
# REDIS_URL=redis://redis:6379   ← "redis" = nom du service dans docker-compose.yml
```

> Si vous ne disposez pas encore des clés API, l'application démarrera en mode dégradé :
> Open-Meteo répondra seul (gratuit, sans clé), les deux autres ouvriront leur circuit breaker
> après 3 erreurs 401.

### 5. Démarrer Redis

```bash
# Option A — via Docker (recommandé, sans installation)
docker run -d --name redis-meteo -p 6379:6379 redis:7-alpine

# Option B — installation système
# Ubuntu/Debian
sudo apt install redis-server && sudo systemctl start redis

# macOS
brew install redis && brew services start redis
```

---

## Lancement local

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Logs de démarrage attendus :

```
2026-06-24 12:00:00 | INFO | app.main      | Démarrage de l'application météo...
2026-06-24 12:00:00 | INFO | app.main      | Application prête. Swagger : /docs | Dashboard : /dashboard
2026-06-24 12:00:00 | INFO | app.scheduler | Scheduler démarré — pré-chauffe toutes les 10 minutes pour 20 villes
```

**Interfaces disponibles après démarrage :**

| URL | Description |
|-----|-------------|
| `http://localhost:8000/docs` | Swagger UI interactif (tester les endpoints directement) |
| `http://localhost:8000/redoc` | Documentation ReDoc (lecture seule, plus lisible) |
| `http://localhost:8000/sante` | État JSON temps réel (circuit breakers + cache) |
| `http://localhost:8000/dashboard` | Dashboard HTML auto-rafraîchissant (10s) |

---

## Lancement avec Docker

### Démarrage standard

```bash
# Première utilisation : configurer .env
cp .env.example .env
# Éditer .env avec les vraies clés API

# Démarrer l'application et Redis
docker compose up
```

Docker Compose orchestre deux services :
- **redis** — Redis 7 Alpine, persistance disque, healthcheck `redis-cli ping`
- **app** — FastAPI via Uvicorn, démarre uniquement quand Redis est sain

### Commandes utiles

```bash
# Reconstruire l'image (obligatoire après modification de requirements.txt ou Dockerfile)
docker compose up --build

# Mode détaché (arrière-plan)
docker compose up -d

# Suivre les logs en temps réel
docker compose logs -f app
docker compose logs -f redis

# Arrêter les services
docker compose down

# Arrêter ET effacer le volume Redis (repart d'un cache vide)
docker compose down -v

# Redémarrer uniquement l'application (sans Redis)
docker compose restart app
```

---

## Utilisation de l'API

### `GET /meteo` — Données météo consolidées

```bash
# Météo de Paris (pays par défaut : FR)
curl "http://localhost:8000/meteo?ville=Paris"

# Avec code pays explicite
curl "http://localhost:8000/meteo?ville=Paris&pays=FR"

# Autres exemples
curl "http://localhost:8000/meteo?ville=Lomé&pays=TG"
curl "http://localhost:8000/meteo?ville=Abidjan&pays=CI"
curl "http://localhost:8000/meteo?ville=New%20York&pays=US"
curl "http://localhost:8000/meteo?ville=Tokyo&pays=JP"

# Affichage formaté
curl -s "http://localhost:8000/meteo?ville=Paris" | python -m json.tool
```

**Paramètres de la requête :**

| Paramètre | Type | Obligatoire | Défaut | Contraintes |
|-----------|------|-------------|--------|-------------|
| `ville` | string | Oui | — | 1 à 100 caractères |
| `pays` | string | Non | `FR` | 2 caractères (ISO 3166-1 alpha-2) |

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
  "depuis_cache": false
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
  "depuis_cache": false
}
```

**Réponse HTTP 200 — depuis le cache Redis :**

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
  "depuis_cache": true
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

**Description des champs de réponse :**

| Champ | Type | Description |
|-------|------|-------------|
| `ville` | string | Nom de la ville tel que transmis |
| `pays` | string | Code pays en majuscules |
| `temperature_c` | float | Température en °C — moyenne des fournisseurs ayant répondu |
| `humidite_pct` | float | Humidité relative en % — moyenne |
| `vent_kmh` | float | Vitesse du vent en km/h — moyenne |
| `description` | string | Condition météo — résultat du vote majoritaire entre fournisseurs |
| `fournisseurs_ok` | array | Identifiants des fournisseurs ayant répondu avec succès |
| `fournisseurs_ko` | array | Identifiants en erreur ou circuit ouvert |
| `nb_sources` | integer | Nombre de fournisseurs ayant contribué à la fusion |
| `depuis_cache` | boolean | `true` si la réponse provient du cache Redis L2 |

---

### `GET /sante` — État de santé

```bash
curl -s "http://localhost:8000/sante" | python -m json.tool
```

```json
{
  "status": "dégradé",
  "fournisseurs": [
    {
      "fournisseur": "openweather",
      "etat": "CLOSED",
      "nb_erreurs": 0,
      "dernier_succes": "2026-06-24T11:42:17"
    },
    {
      "fournisseur": "open_meteo",
      "etat": "CLOSED",
      "nb_erreurs": 0,
      "dernier_succes": "2026-06-24T11:42:17"
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
  "verifie_a": "2026-06-24T11:45:02"
}
```

Valeurs du champ `status` :

| Valeur | Signification |
|--------|---------------|
| `ok` | Les 3 circuit breakers sont CLOSED |
| `dégradé` | 1 ou 2 circuit breakers sont OPEN |
| `critique` | Les 3 circuit breakers sont OPEN |

---

### `GET /dashboard` — Interface de monitoring

Ouvrir dans un navigateur : `http://localhost:8000/dashboard`

Page HTML auto-rafraîchissante toutes les 10 secondes. Affiche :
- Compteurs de hits et misses du cache Redis, ratio en pourcentage
- État en temps réel des 3 circuit breakers avec code couleur (vert / rouge / orange)

---

## Tests

### Lancer la suite complète

```bash
pytest
```

37 tests, durée habituelle < 2 minutes. Aucune dépendance externe : Redis est simulé
via `fakeredis`, les appels HTTP sont interceptés par `respx`.

### Avec couverture de code

```bash
pytest --cov=app --cov-report=term-missing
```

```
Name                           Stmts   Miss  Cover
--------------------------------------------------
app/__init__.py                    0      0   100%
app/cache.py                     145     29    80%
app/circuit_breaker.py            80     16    80%
app/config.py                     16      0   100%
app/main.py                       53     10    81%
app/providers/__init__.py          0      0   100%
app/providers/open_meteo.py       36      1    97%
app/providers/openweather.py      16      0   100%
app/providers/weatherapi.py       19      0   100%
app/router_meteo.py              100     13    87%
app/scheduler.py                  71      6    92%
app/schemas.py                    53      3    94%
--------------------------------------------------
TOTAL                            589     78    87%
```

Couverture actuelle : **87 %** — seuil minimum requis : 70 %.

### Lancer un test ou un module précis

```bash
# Un seul test
pytest tests/test_meteo.py::test_succes_complet -v

# Tous les tests d'un module
pytest tests/test_cache.py -v
pytest tests/test_scheduler.py -v
pytest tests/test_meteo.py -v

# Afficher les 10 tests les plus lents
pytest --durations=10
```

### Scénarios couverts

| Scénario | Module |
|----------|--------|
| Succès avec les 3 fournisseurs | `test_meteo.py` |
| Panne d'un fournisseur (2 répondent) | `test_meteo.py` |
| Panne de deux fournisseurs (1 répond) | `test_meteo.py` |
| Panne totale → HTTP 503 | `test_meteo.py` |
| Réponses très différentes entre fournisseurs (fusion correcte) | `test_meteo.py` |
| Circuit breaker qui s'ouvre après 3 erreurs | `test_meteo.py` |
| Cache L2 hit → zéro appel HTTP | `test_meteo.py` |
| Endpoint `/sante` — statut et circuit breakers | `test_meteo.py` |
| Cache court : écriture, lecture, TTL, normalisation clés | `test_cache.py` |
| Cache long : écriture, lecture, TTL | `test_cache.py` |
| Invalidation, compteurs de popularité, métriques | `test_cache.py` |
| Ratio hit/miss et cas limites (division par zéro) | `test_cache.py` |
| Dégradation gracieuse Redis indisponible | `test_cache.py` |
| Pré-chauffe : cache encore valide → aucun appel HTTP | `test_scheduler.py` |
| Pré-chauffe : 3 providers OK → écriture cache L2 | `test_scheduler.py` |
| Pré-chauffe : tous KO → pas d'exception levée | `test_scheduler.py` |
| Tâche utilise les villes par défaut si Redis vide | `test_scheduler.py` |
| Tâche utilise les villes populaires depuis Redis | `test_scheduler.py` |
| Tâche résiliente : erreur sur une ville n'arrête pas les autres | `test_scheduler.py` |
| Cycle de vie scheduler : démarrer et arrêter proprement | `test_scheduler.py` |

---

## Linting

Le projet utilise `ruff` pour la vérification du style et la détection d'erreurs courantes.

```bash
# Vérifier le code
ruff check .

# Corriger automatiquement ce qui est corrigeable
ruff check . --fix

# Formater le code
ruff format .
```

---

## Structure du projet

```
projet_05_meteo/
│
├── app/                           # Code source
│   ├── __init__.py
│   ├── main.py                    # Point d'entrée FastAPI : lifespan, /sante, /dashboard
│   ├── config.py                  # Toutes les constantes lues depuis l'environnement
│   ├── schemas.py                 # Modèles Pydantic v2 (DonneesMeteo, MeteoResponse…)
│   ├── router_meteo.py            # Route GET /meteo, ServiceCache injectable, orchestration
│   ├── cache.py                   # Redis L1/L2, compteurs popularité, métriques hit/miss
│   ├── circuit_breaker.py         # Classe CircuitBreaker + 3 instances globales
│   ├── scheduler.py               # APScheduler, pré-chauffe des 20 villes / 10 min
│   └── providers/
│       ├── __init__.py
│       ├── openweather.py         # Client API OpenWeatherMap v2.5
│       ├── open_meteo.py          # Client API Open-Meteo (géocodage + météo)
│       └── weatherapi.py          # Client API WeatherAPI current.json
│
├── tests/
│   ├── conftest.py                # Fixtures : TestClient, mocks cache, reset circuit breakers
│   ├── test_meteo.py              # Tests bout en bout de /meteo (9 scénarios)
│   ├── test_cache.py              # Tests unitaires cache Redis — 21 tests avec fakeredis
│   └── test_scheduler.py          # Tests unitaires scheduler — 7 tests avec respx
│
├── .github/
│   └── workflows/
│       └── ci.yml                 # Pipeline CI : lint + tests + build Docker
│
├── Dockerfile                     # Image multi-stage Python 3.11-slim
├── docker-compose.yml             # Orchestration FastAPI + Redis avec healthchecks
├── requirements.txt               # Dépendances Python versionnées
├── pytest.ini                     # Configuration pytest (asyncio_mode=auto)
├── .env.example                   # Modèle de configuration (commité, sans secrets)
├── .env                           # Configuration réelle (ignoré par .gitignore)
└── .gitignore
```

---

## Variables d'environnement

Copier `.env.example` en `.env` et ajuster les valeurs. Seules les clés API
`OPENWEATHER_API_KEY` et `WEATHERAPI_KEY` sont strictement nécessaires pour disposer
des trois fournisseurs.

| Variable | Valeur par défaut | Description |
|----------|-------------------|-------------|
| `OPENWEATHER_API_KEY` | *(vide)* | Clé API OpenWeatherMap — [obtenir gratuitement](https://openweathermap.org/api) |
| `WEATHERAPI_KEY` | *(vide)* | Clé API WeatherAPI — [obtenir gratuitement](https://www.weatherapi.com/) |
| `REDIS_URL` | `redis://localhost:6379` | URL Redis — `redis://localhost:6379` en local, `redis://redis:6379` avec Docker Compose |
| `CACHE_COURT_TTL` | `300` | Durée de vie cache L1 en secondes (5 min) |
| `CACHE_LONG_TTL` | `3600` | Durée de vie cache L2 en secondes (1 h) |
| `PROVIDER_TIMEOUT` | `5.0` | Timeout HTTP par fournisseur en secondes |
| `CIRCUIT_BREAKER_THRESHOLD` | `3` | Nombre d'erreurs avant ouverture du circuit |
| `CIRCUIT_BREAKER_RECOVERY` | `30` | Secondes avant passage OPEN → HALF_OPEN |
| `CIRCUIT_BREAKER_WINDOW` | `60` | Fenêtre glissante de comptage des erreurs (secondes) |
| `TOP_CITIES_COUNT` | `20` | Nombre de villes populaires à pré-chauffer |
| `SCHEDULER_INTERVAL_MINUTES` | `10` | Intervalle entre deux sessions de pré-chauffe |

---

## Dépannage

**`ConnectionRefusedError` au démarrage**

Redis n'est pas accessible sur l'URL configurée. Vérifier que Redis tourne :

```bash
redis-cli ping   # doit répondre PONG
```

Ou lancer Redis via Docker :

```bash
docker run -d --name redis-meteo -p 6379:6379 redis:7-alpine
```

---

**HTTP 503 sur tous les appels `/meteo`**

Les trois circuit breakers sont OPEN. Causes possibles :
- Clés API invalides ou absentes dans `.env`
- Pas de connexion Internet
- Rate limit des APIs gratuites atteint

Vérifier l'état via `GET /sante` et consulter les logs de l'application.

---

**`ValidationError` ou erreur 422 sur `/meteo`**

- `ville` est vide ou dépasse 100 caractères
- `pays` ne fait pas exactement 2 caractères

Exemple valide : `?ville=Paris&pays=FR`

---

**Couverture de tests insuffisante**

Si `pytest --cov=app --cov-fail-under=70` échoue, vérifier que `fakeredis` est bien installé :

```bash
pip install fakeredis==2.26.1
```
