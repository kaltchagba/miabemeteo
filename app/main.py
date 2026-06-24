# ============================================================
# app/main.py — Point d'entrée de l'application FastAPI
# ============================================================
# Mis à jour à l'étape 7 pour intégrer le scheduler APScheduler
# dans le cycle de vie de l'application (startup / shutdown).
# ============================================================

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app import cache
from app.circuit_breaker import circuit_breakers
from app.config import CORS_ORIGINS
from app.router_meteo import router as meteo_router
from app.scheduler import demarrer_scheduler, arreter_scheduler
from app.schemas import SanteResponse, EtatCircuitBreaker

# ---- Configuration du logging ----
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ============================================================
# GESTIONNAIRE DU CYCLE DE VIE (startup / shutdown)
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup : vérifie Redis, démarre le scheduler.
    Shutdown : arrête le scheduler, ferme le client HTTP.
    """
    # --- STARTUP ---
    logger.info("Démarrage de l'application météo...")

    # Vérification Redis (non bloquant si Redis absent)
    stats = cache.obtenir_stats()
    logger.info("Redis — stats cache initiaux : %s", stats)

    # Démarrage du scheduler de pré-chauffe (thread séparé, non bloquant)
    demarrer_scheduler()

    logger.info("Application prête. Swagger : /docs | Dashboard : /dashboard")

    yield   # L'application tourne ici

    # --- SHUTDOWN ---
    logger.info("Arrêt de l'application...")

    # Arrêt propre du scheduler (attend la fin de la tâche en cours)
    arreter_scheduler()

    # Fermeture propre du client httpx partagé
    from app.router_meteo import _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        logger.info("Client HTTP fermé proprement.")


# ============================================================
# CRÉATION DE L'APPLICATION FASTAPI
# ============================================================
app = FastAPI(
    title="Agrégateur Météo Multi-Sources",
    description=(
        "Interroge **OpenWeather**, **Open-Meteo** et **WeatherAPI** en parallèle, "
        "fusionne les résultats et les met en cache Redis.\n\n"
        "- Cache court : 5 min par fournisseur\n"
        "- Cache long : 1 h (réponse consolidée)\n"
        "- Circuit breaker par fournisseur (CLOSED / OPEN / HALF_OPEN)\n"
        "- Pré-chauffe automatique des 20 villes populaires toutes les 10 min"
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# ---- Middleware CORS ----
# Autorise les navigateurs à interroger l'API depuis d'autres origines.
# CORS_ORIGINS est configurable via la variable d'environnement du même nom.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS.split(","),
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ---- Montage des routers ----
app.include_router(meteo_router)   # /meteo


# ============================================================
# ROUTE /sante
# ============================================================
@app.get(
    "/sante",
    response_model=SanteResponse,
    tags=["Infrastructure"],
    summary="État de santé de l'application",
)
def sante() -> SanteResponse:
    """
    Retourne l'état de chaque circuit breaker et les métriques de cache Redis.
    Utilisé par Docker healthcheck et les outils de monitoring.
    """
    # ---- Circuit breakers ----
    etats_cb = []
    for provider_id, cb in circuit_breakers.items():
        # Conversion timestamp monotonic → datetime UTC si disponible
        dernier_succes_dt = None
        if cb.dernier_succes is not None:
            secondes = time.monotonic() - cb.dernier_succes
            dernier_succes_dt = datetime.utcnow() - timedelta(seconds=secondes)

        etats_cb.append(EtatCircuitBreaker(
            fournisseur=provider_id,
            etat=cb.etat.value,
            nb_erreurs=cb.nb_erreurs_recentes,
            dernier_succes=dernier_succes_dt,
        ))

    # ---- Statut global ----
    nb_open = sum(1 for cb in circuit_breakers.values() if cb.etat.value == "OPEN")
    statut = "ok" if nb_open == 0 else ("dégradé" if nb_open < 3 else "critique")

    # ---- Métriques Redis ----
    stats = cache.obtenir_stats()

    return SanteResponse(
        status=statut,
        fournisseurs=etats_cb,
        cache_hits=stats["hits"],
        cache_misses=stats["misses"],
        verifie_a=datetime.utcnow(),
    )


# ============================================================
# ROUTE /dashboard
# ============================================================
@app.get(
    "/dashboard",
    response_class=HTMLResponse,
    tags=["Infrastructure"],
    summary="Dashboard HTML des métriques",
    include_in_schema=False,
)
def dashboard() -> HTMLResponse:
    """
    Page HTML auto-rafraîchissante toutes les 10 secondes.
    Affiche les métriques de cache et l'état des circuit breakers.
    """
    stats = cache.obtenir_stats()

    def couleur(etat: str) -> str:
        """Retourne la couleur CSS selon l'état du circuit breaker."""
        return {"CLOSED": "#22c55e", "OPEN": "#ef4444", "HALF_OPEN": "#f59e0b"}.get(etat, "#6b7280")

    # Construction du HTML des circuit breakers
    cb_html = "".join(
        f'<div class="cb">'
        f'<span class="dot" style="background:{couleur(cb.etat.value)}"></span>'
        f'<strong>{pid}</strong>'
        f'<span class="etat">{cb.etat.value}</span>'
        f'<span class="erreurs">{cb.nb_erreurs_recentes} erreur(s)</span>'
        f'</div>'
        for pid, cb in circuit_breakers.items()
    )

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="10">
  <title>Dashboard Météo</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, sans-serif; background: #0f172a;
            color: #e2e8f0; padding: 2rem; }}
    h1 {{ color: #38bdf8; margin-bottom: .25rem; font-size: 1.75rem; }}
    .sub {{ color: #64748b; margin-bottom: 2rem; font-size: .9rem; }}
    h2 {{ color: #94a3b8; font-size: 1rem; text-transform: uppercase;
           letter-spacing: .1em; margin: 1.5rem 0 .75rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; }}
    .card {{ background: #1e293b; border-radius: 12px; padding: 1.5rem;
             text-align: center; border: 1px solid #334155; }}
    .val {{ font-size: 2.5rem; font-weight: 700; color: #38bdf8; }}
    .lbl {{ color: #94a3b8; margin-top: .5rem; font-size: .875rem; }}
    .cb  {{ background: #1e293b; border-radius: 8px; padding: .875rem 1rem;
            margin-bottom: .5rem; display: flex; align-items: center; gap: .75rem;
            border: 1px solid #334155; }}
    .dot {{ width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }}
    .etat {{ margin-left: auto; font-size: .8rem; font-weight: 600;
             background: #0f172a; padding: .2rem .6rem; border-radius: 999px; }}
    .erreurs {{ color: #64748b; font-size: .8rem; }}
    .note {{ color: #475569; font-size: .8rem; margin-top: 2rem; }}
  </style>
</head>
<body>
  <h1>🌤 Dashboard Agrégateur Météo</h1>
  <p class="sub">Rafraîchissement automatique toutes les 10 secondes</p>

  <h2>Métriques Cache Redis</h2>
  <div class="grid">
    <div class="card">
      <div class="val">{stats['hits']}</div>
      <div class="lbl">Cache Hits</div>
    </div>
    <div class="card">
      <div class="val">{stats['misses']}</div>
      <div class="lbl">Cache Misses</div>
    </div>
    <div class="card">
      <div class="val">{stats['ratio_pct']}%</div>
      <div class="lbl">Ratio Hit</div>
    </div>
  </div>

  <h2>Circuit Breakers</h2>
  {cb_html}

  <p class="note">
    Généré le {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
    · Scheduler actif — pré-chauffe des villes populaires toutes les 10 min
  </p>
</body>
</html>"""
    return HTMLResponse(content=html)


# ============================================================
# LANCEMENT DIRECT (développement)
# ============================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)