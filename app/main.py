import base64
import logging
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse
from prometheus_fastapi_instrumentator import Instrumentator
from pythonjsonlogger import jsonlogger

from app import cache
from app.circuit_breaker import circuit_breakers
from app.config import CORS_ORIGINS
from app.metrics import maj_circuit_breaker
from app.middleware import RateLimitMiddleware
from app.router_meteo import router as meteo_router
from app.router_donnees import router as donnees_router
from app.scheduler import demarrer_scheduler, arreter_scheduler
from app.schemas import SanteResponse, EtatCircuitBreaker

_handler = logging.StreamHandler()
_handler.setFormatter(
    jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
)
logging.root.setLevel(logging.INFO)
logging.root.addHandler(_handler)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Démarrage de l'application météo...")
    stats = cache.obtenir_stats()
    logger.info("Redis — stats cache initiaux : %s", stats)
    demarrer_scheduler()
    logger.info("Application prête. Swagger : /docs | Dashboard : /dashboard")

    yield

    logger.info("Arrêt de l'application...")
    arreter_scheduler()
    from app.router_meteo import fermer_client_http

    await fermer_client_http()
    logger.info("Client HTTP fermé.")


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
    docs_url=None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS.split(","),
    allow_methods=["GET", "POST"],  # POST requis pour /batch
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)

app.include_router(meteo_router)
app.include_router(donnees_router)

Instrumentator(excluded_handlers=["/metrics"]).instrument(app).expose(app)


@app.get("/docs", include_in_schema=False, tags=["Infrastructure"])
def swagger_ui() -> HTMLResponse:
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="Agrégateur Météo Multi-Sources — Swagger UI",
        swagger_js_url="https://unpkg.com/swagger-ui-dist@5.18.2/swagger-ui-bundle.js",
        swagger_css_url="https://unpkg.com/swagger-ui-dist@5.18.2/swagger-ui.css",
    )


@app.get("/redoc", include_in_schema=False, tags=["Infrastructure"])
def redoc_ui() -> HTMLResponse:
    return get_redoc_html(
        openapi_url="/openapi.json",
        title="Agrégateur Météo Multi-Sources — ReDoc",
        redoc_js_url="https://unpkg.com/redoc@2.1.5/bundles/redoc.standalone.js",
    )


@app.get(
    "/sante",
    response_model=SanteResponse,
    tags=["Infrastructure"],
    summary="État de santé de l'application",
)
def sante() -> SanteResponse:
    """Retourne l'état des circuit breakers et les métriques Redis. Utilisé par le healthcheck Docker."""
    etats_cb = []
    for provider_id, cb in circuit_breakers.items():
        dernier_succes_dt = None
        if cb.dernier_succes is not None:
            secondes = time.monotonic() - cb.dernier_succes
            dernier_succes_dt = datetime.now(timezone.utc) - timedelta(seconds=secondes)

        etats_cb.append(
            EtatCircuitBreaker(
                fournisseur=provider_id,
                etat=cb.etat.value,
                nb_erreurs=cb.nb_erreurs_recentes,
                dernier_succes=dernier_succes_dt,
            )
        )
        maj_circuit_breaker(provider_id, cb.etat.value)

    nb_open = sum(1 for cb in circuit_breakers.values() if cb.etat.value == "OPEN")
    statut = "ok" if nb_open == 0 else ("dégradé" if nb_open < 3 else "critique")
    stats = cache.obtenir_stats()

    return SanteResponse(
        status=statut,
        fournisseurs=etats_cb,
        cache_hits=stats["hits"],
        cache_misses=stats["misses"],
        verifie_a=datetime.now(timezone.utc),
    )


@app.get(
    "/dashboard",
    response_class=HTMLResponse,
    tags=["Infrastructure"],
    summary="Dashboard HTML des métriques",
    include_in_schema=False,
)
def dashboard() -> HTMLResponse:
    """Page HTML auto-rafraîchissante toutes les 10 secondes."""
    stats = cache.obtenir_stats()

    def couleur(etat: str) -> str:
        return {"CLOSED": "#22c55e", "OPEN": "#ef4444", "HALF_OPEN": "#f59e0b"}.get(
            etat, "#6b7280"
        )

    cb_html = "".join(
        f'<div class="cb">'
        f'<span class="dot" style="background:{couleur(cb.etat.value)}"></span>'
        f"<strong>{pid}</strong>"
        f'<span class="etat">{cb.etat.value}</span>'
        f'<span class="erreurs">{cb.nb_erreurs_recentes} erreur(s)</span>'
        f"</div>"
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
    Généré le {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC
    · Scheduler actif — pré-chauffe des villes populaires toutes les 10 min
  </p>
</body>
</html>"""
    return HTMLResponse(content=html)


@app.get(
    "/carte",
    response_class=HTMLResponse,
    tags=["Infrastructure"],
    summary="Carte météo mondiale",
    include_in_schema=False,
)
def carte() -> HTMLResponse:
    """Redirige vers /interface pour compatibilité."""
    return HTMLResponse(
        content='<meta http-equiv="refresh" content="0;url=/interface#monde">',
        status_code=302,
    )


@app.get(
    "/interface",
    response_class=HTMLResponse,
    tags=["Infrastructure"],
    summary="Interface météo interactive — 5 onglets",
    include_in_schema=False,
)
def interface() -> HTMLResponse:
    html_file = Path(__file__).parent / "templates" / "interface.html"
    html = html_file.read_text(encoding="utf-8")

    nonce = base64.b64encode(secrets.token_bytes(18)).decode()
    html = html.replace("__CSP_NONCE__", nonce)

    csp = (
        "default-src 'self'; "
        f"script-src 'self' https://unpkg.com 'nonce-{nonce}'; "
        "script-src-attr 'none'; "
        "style-src 'self' https://unpkg.com 'unsafe-inline'; "
        "img-src 'self' https://*.basemaps.cartocdn.com "
        "https://*.tile.openstreetmap.org data: blob:; "
        "connect-src 'self' "
        "https://geocoding-api.open-meteo.com "
        "https://nominatim.openstreetmap.org "
        "wss: ws:; "
        "worker-src blob: 'self'; "
        "font-src 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )
    return HTMLResponse(
        content=html,
        headers={"Content-Security-Policy": csp},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
