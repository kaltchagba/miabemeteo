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
from fastapi.responses import HTMLResponse, RedirectResponse
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


@app.get("/", include_in_schema=False)
def accueil() -> RedirectResponse:
    return RedirectResponse(url="/interface", status_code=302)


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
        f'<span class="cb-name">{pid}</span>'
        f'<span class="erreurs">{cb.nb_erreurs_recentes} erreur(s)</span>'
        f'<span class="etat">{cb.etat.value}</span>'
        f"</div>"
        for pid, cb in circuit_breakers.items()
    )

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <meta http-equiv="refresh" content="10">
  <title>Dashboard — MiabeMETEO</title>
  <style>
    *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;
          background:#06090f;color:#d8e2f0;min-height:100vh;padding:2rem 1.5rem 3rem}}
    nav{{display:flex;align-items:center;justify-content:space-between;
         margin-bottom:2rem;padding-bottom:1rem;
         border-bottom:1px solid rgba(255,255,255,.07)}}
    .logo{{font-size:1rem;font-weight:700;color:#00c4a7;letter-spacing:.02em}}
    .logo span{{color:#d8e2f0;font-weight:400}}
    .back{{font-size:.78rem;color:#4d6080;text-decoration:none;
           padding:.3rem .8rem;border:1px solid rgba(255,255,255,.07);
           border-radius:6px;transition:color .15s,border-color .15s}}
    .back:hover{{color:#00c4a7;border-color:rgba(0,196,167,.3)}}
    h2{{font-size:.72rem;font-weight:600;text-transform:uppercase;
        letter-spacing:.1em;color:#4d6080;margin:1.75rem 0 .75rem}}
    .grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:.875rem}}
    .card{{background:rgba(10,16,28,.97);border:1px solid rgba(255,255,255,.07);
           border-radius:10px;padding:1.375rem 1rem;text-align:center}}
    .val{{font-size:2.25rem;font-weight:700;color:#00c4a7;
          font-variant-numeric:tabular-nums;line-height:1}}
    .lbl{{color:#4d6080;margin-top:.5rem;font-size:.8rem}}
    .cb{{background:rgba(10,16,28,.97);border:1px solid rgba(255,255,255,.07);
         border-radius:8px;padding:.875rem 1rem;margin-bottom:.5rem;
         display:flex;align-items:center;gap:.75rem}}
    .dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}
    .cb-name{{font-size:.875rem;font-weight:500}}
    .etat{{margin-left:auto;font-size:.72rem;font-weight:600;
           padding:.2rem .65rem;border-radius:999px;
           background:rgba(0,0,0,.35);letter-spacing:.03em}}
    .erreurs{{color:#4d6080;font-size:.75rem}}
    .note{{color:#4d6080;font-size:.75rem;margin-top:2rem;line-height:1.6}}
  </style>
</head>
<body>
  <nav>
    <div class="logo">MiabeMETEO <span>· Dashboard</span></div>
    <a class="back" href="/interface">← Interface</a>
  </nav>

  <h2>Cache Redis</h2>
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
    · Rafraîchissement toutes les 10 s · Scheduler actif — pré-chauffe toutes les 10 min
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
