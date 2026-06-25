# ============================================================
# app/main.py — Point d'entrée de l'application FastAPI
# ============================================================
# Mis à jour à l'étape 7 pour intégrer le scheduler APScheduler
# dans le cycle de vie de l'application (startup / shutdown).
# ============================================================

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from prometheus_fastapi_instrumentator import Instrumentator
from pythonjsonlogger import jsonlogger

from app import cache
from app.circuit_breaker import circuit_breakers
from app.config import CORS_ORIGINS
from app.metrics import maj_circuit_breaker
from app.router_meteo import router as meteo_router
from app.scheduler import demarrer_scheduler, arreter_scheduler
from app.schemas import SanteResponse, EtatCircuitBreaker

# ---- Configuration du logging JSON structuré ----
# Chaque ligne de log est un objet JSON parsable par les outils de monitoring
# (ELK, Grafana Loki, Datadog...).
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
    from app.router_meteo import fermer_client_http
    await fermer_client_http()
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

# ---- Métriques Prometheus ----
# Instrumente automatiquement toutes les routes HTTP (compteurs + histogrammes)
# et expose l'endpoint /metrics au format text/plain attendu par Prometheus.
Instrumentator(excluded_handlers=["/metrics"]).instrument(app).expose(app)


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
            dernier_succes_dt = datetime.now(timezone.utc) - timedelta(seconds=secondes)

        etats_cb.append(EtatCircuitBreaker(
            fournisseur=provider_id,
            etat=cb.etat.value,
            nb_erreurs=cb.nb_erreurs_recentes,
            dernier_succes=dernier_succes_dt,
        ))
        # Synchronisation de la jauge Prometheus avec l'état réel
        maj_circuit_breaker(provider_id, cb.etat.value)

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
        verifie_a=datetime.now(timezone.utc),
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
    Généré le {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC
    · Scheduler actif — pré-chauffe des villes populaires toutes les 10 min
  </p>
</body>
</html>"""
    return HTMLResponse(content=html)


# ============================================================
# ROUTE /interface — Interface web interactive
# ============================================================
@app.get(
    "/interface",
    response_class=HTMLResponse,
    tags=["Infrastructure"],
    summary="Interface météo interactive",
    include_in_schema=False,
)
def interface() -> HTMLResponse:
    html = """<!DOCTYPE html>
<html lang="fr" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Météo · Agrégateur</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#07090f;--surface:#0d1117;--card:#111827;--card2:#151f2e;
  --border:#1e2d45;--accent:#60a5fa;--accent2:#a78bfa;
  --green:#34d399;--red:#fb7185;--orange:#fbbf24;
  --text:#f1f5f9;--text2:#8ba3c0;--text3:#3d5269;
  --radius:20px;--tr:.25s cubic-bezier(.4,0,.2,1);
  --glow:0 0 40px rgba(96,165,250,.08);
  --shadow:0 8px 32px rgba(0,0,0,.5);
}
[data-theme="light"]{
  --bg:#eef2f7;--surface:#ffffff;--card:#ffffff;--card2:#f4f7fb;
  --border:#d1dce9;--accent:#2563eb;--accent2:#7c3aed;
  --green:#059669;--red:#e11d48;--orange:#d97706;
  --text:#0c1526;--text2:#4a6080;--text3:#94aabb;
  --glow:0 0 40px rgba(37,99,235,.05);
  --shadow:0 8px 32px rgba(0,0,0,.1);
}

html { font-size:16px }
body{
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;
  background:var(--bg);color:var(--text);min-height:100vh;
  transition:background var(--tr),color var(--tr);line-height:1.55;
}

/* NAV */
nav{
  position:sticky;top:0;z-index:200;height:52px;
  display:flex;align-items:center;padding:0 1.5rem;gap:.75rem;
  background:rgba(7,9,15,.82);border-bottom:1px solid var(--border);
  backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);
  transition:background var(--tr),border-color var(--tr);
}
[data-theme="light"] nav{background:rgba(238,242,247,.88)}
.brand{display:flex;align-items:center;gap:.5rem;font-weight:700;
        font-size:.95rem;color:var(--text);text-decoration:none}
.brand-icon{font-size:1.25rem}
.nav-links{display:flex;align-items:center;gap:.15rem;margin-left:auto}
.nav-links a,.nav-btn{
  color:var(--text2);text-decoration:none;font-size:.78rem;font-weight:500;
  padding:.3rem .65rem;border-radius:8px;border:none;background:none;cursor:pointer;
  transition:background var(--tr),color var(--tr);white-space:nowrap;font-family:inherit;
}
.nav-links a:hover,.nav-btn:hover{background:var(--card2);color:var(--text)}
.sep-v{width:1px;height:16px;background:var(--border);margin:0 .2rem}

/* THEME SWITCH */
.switch{
  position:relative;width:40px;height:22px;cursor:pointer;
  background:var(--card2);border:1px solid var(--border);border-radius:999px;
  transition:background var(--tr);flex-shrink:0;
}
.switch::after{
  content:'';position:absolute;top:2px;left:2px;
  width:16px;height:16px;border-radius:50%;
  background:var(--accent);
  transition:transform var(--tr),background var(--tr);
}
[data-theme="light"] .switch::after{transform:translateX(18px);background:var(--orange)}
.switch-label{font-size:.72rem;color:var(--text3);white-space:nowrap}

/* MAIN WRAPPER */
.wrap{max-width:860px;margin:0 auto;padding:2.5rem 1.25rem 5rem}

/* SEARCH */
.search-area{text-align:center;margin-bottom:2.5rem}
.search-tagline{
  font-size:clamp(1.5rem,3.5vw,2.2rem);font-weight:800;
  letter-spacing:-.03em;margin-bottom:.4rem;line-height:1.2;
}
.search-tagline em{
  font-style:normal;
  background:linear-gradient(120deg,var(--accent),var(--accent2));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
}
.search-sub{color:var(--text2);font-size:.875rem;margin-bottom:1.5rem}
.search-row{
  display:flex;max-width:540px;margin:0 auto;
  background:var(--card);border:1.5px solid var(--border);
  border-radius:16px;overflow:hidden;
  box-shadow:var(--shadow);transition:border-color var(--tr),box-shadow var(--tr);
}
.search-row:focus-within{
  border-color:var(--accent);
  box-shadow:0 0 0 4px color-mix(in srgb,var(--accent) 12%,transparent);
}
.search-row input{
  border:none;outline:none;background:transparent;color:var(--text);
  font-size:.95rem;padding:.8rem 1rem;font-family:inherit;
}
.search-row .inp-v{flex:1;min-width:0}
.search-row .inp-p{
  width:56px;text-align:center;text-transform:uppercase;
  font-weight:700;color:var(--accent);letter-spacing:.05em;
  border-left:1px solid var(--border);
}
.search-row button{
  background:linear-gradient(135deg,var(--accent),var(--accent2));
  color:#fff;border:none;padding:.8rem 1.4rem;font-size:.875rem;font-weight:700;
  cursor:pointer;white-space:nowrap;font-family:inherit;flex-shrink:0;
  transition:filter var(--tr);
}
.search-row button:hover{filter:brightness(1.1)}
.search-row button:disabled{opacity:.4;cursor:not-allowed;filter:none}

/* LOADER */
.loader{display:none;align-items:center;justify-content:center;gap:.5rem;
         padding:1.25rem;color:var(--text2);font-size:.875rem}
.loader.on{display:flex}
.dot{width:6px;height:6px;border-radius:50%;background:var(--accent);animation:bop .8s infinite}
.dot:nth-child(2){animation-delay:.15s}
.dot:nth-child(3){animation-delay:.3s}
@keyframes bop{0%,60%,100%{transform:translateY(0)}30%{transform:translateY(-7px)}}

/* ALERTS */
.alert{
  border-radius:14px;padding:.9rem 1.1rem;margin-bottom:1rem;
  font-size:.875rem;line-height:1.5;display:none;gap:.6rem;align-items:flex-start;
}
.alert.on{display:flex}
.alert-err{
  background:color-mix(in srgb,var(--red) 9%,transparent);
  border:1px solid color-mix(in srgb,var(--red) 30%,transparent);color:var(--red);
}
.alert-warn{
  background:color-mix(in srgb,var(--orange) 9%,transparent);
  border:1px solid color-mix(in srgb,var(--orange) 30%,transparent);color:var(--orange);
}
.alert-icon{flex-shrink:0;font-size:1rem}

/* WEATHER CARD */
.card{
  display:none;border-radius:var(--radius);overflow:hidden;
  border:1px solid var(--border);box-shadow:var(--shadow),var(--glow);
  margin-bottom:1.25rem;animation:up .4s cubic-bezier(.4,0,.2,1);
}
.card.on{display:block}
@keyframes up{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}

/* card hero — immersive top */
.card-top{
  padding:2.25rem 2rem 1.75rem;position:relative;overflow:hidden;
  background:var(--card-grad,var(--card));
  border-bottom:1px solid var(--border);
}
.card-top::before{
  content:'';position:absolute;inset:0;
  background:radial-gradient(ellipse 80% 60% at 70% 0%,
    color-mix(in srgb,var(--accent) 15%,transparent),transparent 70%);
  pointer-events:none;
}
.card-top-inner{position:relative;display:flex;gap:1.5rem;align-items:flex-start;flex-wrap:wrap}
.card-meta{flex:1;min-width:0}
.city-line{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;margin-bottom:.3rem}
.city{font-size:2rem;font-weight:800;letter-spacing:-.03em;line-height:1}
.ctag{
  font-size:.65rem;font-weight:800;letter-spacing:.1em;
  background:color-mix(in srgb,var(--accent) 16%,transparent);
  color:var(--accent);border:1px solid color-mix(in srgb,var(--accent) 28%,transparent);
  border-radius:6px;padding:.12rem .5rem;flex-shrink:0;
}
.cdesc{color:var(--text2);font-size:.9rem;text-transform:capitalize;margin-bottom:.9rem}
.live-chip{
  display:none;align-items:center;gap:.35rem;
  background:color-mix(in srgb,var(--green) 14%,transparent);
  border:1px solid color-mix(in srgb,var(--green) 28%,transparent);
  color:var(--green);border-radius:999px;
  font-size:.68rem;font-weight:800;padding:.18rem .6rem;letter-spacing:.06em;
}
.live-chip.on{display:inline-flex}
.ldot{width:5px;height:5px;border-radius:50%;background:currentColor;animation:pulse 1.2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.2}}

.card-right{text-align:right;flex-shrink:0}
.w-emoji{font-size:4rem;display:block;line-height:1;filter:drop-shadow(0 4px 12px rgba(0,0,0,.3))}
.temp{font-size:4.5rem;font-weight:900;letter-spacing:-.05em;line-height:1;margin-top:.2rem}
.src-count{color:var(--text3);font-size:.75rem;margin-top:.3rem}

/* stats row */
.stats{display:grid;grid-template-columns:repeat(3,1fr);background:var(--border);gap:1px}
.stat{background:var(--card2);padding:1.1rem 1.25rem;display:flex;flex-direction:column;gap:.2rem}
.stat-k{font-size:.65rem;font-weight:700;text-transform:uppercase;letter-spacing:.09em;color:var(--text3)}
.stat-v{font-size:1.25rem;font-weight:800;color:var(--text)}
.stat-u{font-size:.78rem;font-weight:400;color:var(--text2)}

/* conf bar (full width stat) */
.stat-conf{grid-column:span 3;flex-direction:row;align-items:center;gap:1rem}
.conf-num{font-size:1.5rem;font-weight:800;min-width:56px}
.conf-bar-wrap{flex:1}
.track{height:5px;background:var(--border);border-radius:999px;overflow:hidden;margin-bottom:.3rem}
.fill{height:100%;border-radius:999px;transition:width .8s cubic-bezier(.4,0,.2,1),background .3s}
.conf-lbl{font-size:.7rem;color:var(--text3)}

/* card bottom */
.card-bottom{padding:1.25rem 1.75rem;display:flex;flex-direction:column;gap:1rem;
              background:var(--card)}
.prov-strip{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}
.prov-k{font-size:.65rem;font-weight:700;text-transform:uppercase;letter-spacing:.09em;
         color:var(--text3);margin-right:.25rem}
.pill{
  display:inline-flex;align-items:center;gap:.28rem;
  border-radius:999px;font-size:.72rem;font-weight:700;
  padding:.2rem .7rem;border:1px solid transparent;
}
.pill-ok{
  background:color-mix(in srgb,var(--green) 11%,transparent);
  border-color:color-mix(in srgb,var(--green) 25%,transparent);color:var(--green);
}
.pill-ko{
  background:color-mix(in srgb,var(--red) 9%,transparent);
  border-color:color-mix(in srgb,var(--red) 22%,transparent);color:var(--red);
}
.pdot{width:4px;height:4px;border-radius:50%;background:currentColor}

.actions{display:flex;gap:.6rem;flex-wrap:wrap}
.btn{
  display:inline-flex;align-items:center;gap:.4rem;
  border:1px solid var(--border);background:transparent;
  color:var(--text2);border-radius:10px;padding:.5rem .95rem;
  font-size:.8rem;font-weight:600;cursor:pointer;font-family:inherit;
  transition:all var(--tr);white-space:nowrap;
}
.btn:hover{border-color:var(--accent);color:var(--accent);background:color-mix(in srgb,var(--accent) 7%,transparent)}
.btn.on-compare{border-color:var(--accent2);color:var(--accent2);background:color-mix(in srgb,var(--accent2) 9%,transparent)}
.btn.on-live{border-color:var(--green);color:var(--green);background:color-mix(in srgb,var(--green) 9%,transparent)}
.btn svg{width:13px;height:13px;flex-shrink:0}

.ts{font-size:.7rem;color:var(--text3)}
.chip-cache{
  display:inline-flex;align-items:center;gap:.25rem;
  background:color-mix(in srgb,var(--accent) 10%,transparent);
  color:var(--accent);border-radius:5px;padding:.03rem .38rem;
  font-size:.62rem;font-weight:700;letter-spacing:.05em;
}

/* COMPARE PANEL */
.cmp-panel{
  display:none;background:var(--card);border:1px solid var(--border);
  border-radius:var(--radius);box-shadow:var(--shadow);
  overflow:hidden;margin-bottom:1.25rem;animation:up .3s cubic-bezier(.4,0,.2,1);
}
.cmp-panel.on{display:block}
.cmp-head{
  display:flex;align-items:center;justify-content:space-between;
  padding:1rem 1.5rem;border-bottom:1px solid var(--border);background:var(--card2);
}
.cmp-title{font-size:.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.09em;color:var(--text2)}
.cmp-close{background:none;border:none;cursor:pointer;color:var(--text3);
            font-size:1rem;border-radius:6px;padding:.2rem .4rem;
            transition:color var(--tr),background var(--tr);font-family:inherit}
.cmp-close:hover{color:var(--text);background:var(--border)}
.cmp-scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:.83rem}
thead th{
  text-align:left;padding:.65rem 1.1rem;
  font-size:.65rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;
  color:var(--text3);background:var(--card2);border-bottom:1px solid var(--border);white-space:nowrap;
}
tbody td{padding:.75rem 1.1rem;border-bottom:1px solid var(--border);vertical-align:middle}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover td{background:color-mix(in srgb,var(--accent) 4%,transparent)}
.td-name{font-weight:700;color:var(--text)}
.td-n{font-variant-numeric:tabular-nums}
.td-d{color:var(--text2);font-size:.78rem;max-width:150px}
.td-ok{color:var(--green);font-weight:700}
.td-ko{color:var(--red);opacity:.55}
.ecart-tr td{color:var(--text3);font-size:.74rem;font-style:italic;
             background:color-mix(in srgb,var(--border) 35%,transparent);border-bottom:none}
.cmp-footer{
  padding:1.1rem 1.5rem;border-top:1px solid var(--border);background:var(--card2);
  display:flex;align-items:center;gap:1rem;
}
.csc{font-size:1.4rem;font-weight:800;min-width:52px}
.csc-wrap{flex:1}
.csc-sub{font-size:.7rem;color:var(--text3);margin-top:.3rem}

/* FOOTER */
footer{
  margin-top:3rem;padding-top:1.5rem;border-top:1px solid var(--border);
  display:flex;gap:.75rem;justify-content:center;flex-wrap:wrap;
}
footer a{
  display:flex;align-items:center;gap:.35rem;color:var(--text3);text-decoration:none;
  font-size:.75rem;padding:.3rem .6rem;border-radius:8px;font-weight:500;
  transition:color var(--tr),background var(--tr);
}
footer a:hover{color:var(--text);background:var(--card2)}

@media(max-width:540px){
  .card-top{padding:1.5rem 1.25rem 1.25rem}
  .card-bottom{padding:1rem 1.25rem}
  .temp{font-size:3rem}
  .stats{grid-template-columns:repeat(2,1fr)}
  .stat-conf{grid-column:span 2}
}
</style>
</head>
<body>

<nav>
  <a class="brand" href="/interface">
    <span class="brand-icon">🌤</span>
    <span>MétéoAgreg</span>
  </a>
  <div class="nav-links">
    <a href="/docs" target="_blank">API</a>
    <span class="sep-v"></span>
    <a href="/dashboard" target="_blank">Dashboard</a>
    <a href="/sante" target="_blank">Santé</a>
    <span class="sep-v"></span>
    <span class="switch-label" id="themeLabel">🌙</span>
    <button class="switch" id="themeBtn" title="Basculer jour / nuit" aria-label="Toggle thème"></button>
  </div>
</nav>

<div class="wrap">

  <div class="search-area">
    <h1 class="search-tagline">La météo <em>fiable</em>,<br>vue par 3 sources.</h1>
    <p class="search-sub">OpenWeather · Open-Meteo · WeatherAPI — fusion et consensus en temps réel</p>
    <div class="search-row">
      <input class="inp-v" id="ville" type="text" placeholder="Paris, Tokyo, Lomé…"
             value="Paris" autocomplete="off" onkeydown="if(event.key==='Enter')chercher()">
      <input class="inp-p" id="pays" type="text" placeholder="FR" value="FR" maxlength="2"
             onkeydown="if(event.key==='Enter')chercher()">
      <button id="btnSearch" onclick="chercher()">Rechercher →</button>
    </div>
  </div>

  <div class="loader" id="loader">
    <div class="dot"></div><div class="dot"></div><div class="dot"></div>
    <span>Interrogation des fournisseurs…</span>
  </div>

  <div class="alert alert-err" id="alertErr"><span class="alert-icon">✕</span><span id="alertErrTxt"></span></div>
  <div class="alert alert-warn" id="alertWarn"><span class="alert-icon">⚠</span><span id="alertWarnTxt"></span></div>

  <!-- Carte météo -->
  <div class="card" id="card">

    <div class="card-top" id="cardTop">
      <div class="card-top-inner">
        <div class="card-meta">
          <div class="city-line">
            <span class="city" id="cityName">—</span>
            <span class="ctag" id="ctag">—</span>
          </div>
          <div class="cdesc" id="cdesc">—</div>
          <span class="live-chip" id="liveChip"><span class="ldot"></span>LIVE</span>
        </div>
        <div class="card-right">
          <span class="w-emoji" id="wEmoji">🌤</span>
          <div class="temp" id="temp">—</div>
          <div class="src-count" id="srcCount">—</div>
        </div>
      </div>
    </div>

    <div class="stats">
      <div class="stat">
        <span class="stat-k">Humidité</span>
        <span class="stat-v"><span id="humidity">—</span><span class="stat-u"> %</span></span>
      </div>
      <div class="stat">
        <span class="stat-k">Vent</span>
        <span class="stat-v"><span id="wind">—</span><span class="stat-u"> km/h</span></span>
      </div>
      <div class="stat">
        <span class="stat-k">Sources actives</span>
        <span class="stat-v" id="nbOk">—</span>
      </div>
      <div class="stat stat-conf">
        <div>
          <span class="stat-k">Confiance inter-sources</span>
          <div class="conf-num" id="confNum">—</div>
        </div>
        <div class="conf-bar-wrap">
          <div class="track"><div class="fill" id="confFill" style="width:0%"></div></div>
          <div class="conf-lbl" id="confLbl">—</div>
        </div>
      </div>
    </div>

    <div class="card-bottom">
      <div class="prov-strip">
        <span class="prov-k">Sources</span>
        <div id="pills"></div>
      </div>

      <div class="actions">
        <button class="btn" id="btnCmp" onclick="toggleCmp()">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8">
            <rect x="1" y="4" width="5" height="8" rx="1"/>
            <rect x="10" y="2" width="5" height="12" rx="1"/>
            <line x1="6" y1="8" x2="10" y2="8"/>
          </svg>
          Comparer les sources
        </button>
        <button class="btn" id="btnLive" onclick="toggleLive()">
          <svg viewBox="0 0 16 16" fill="currentColor">
            <circle cx="8" cy="8" r="2.5"/>
            <path d="M5.5 5.5a3.5 3.5 0 0 0 0 5M10.5 5.5a3.5 3.5 0 0 1 0 5" stroke="currentColor" stroke-width="1.5" fill="none"/>
            <path d="M3 3a7 7 0 0 0 0 10M13 3a7 7 0 0 1 0 10" stroke="currentColor" stroke-width="1.4" fill="none"/>
          </svg>
          Flux live (30s)
        </button>
      </div>

      <div class="ts" id="ts"></div>
    </div>
  </div>

  <!-- Comparaison -->
  <div class="cmp-panel" id="cmpPanel">
    <div class="cmp-head">
      <span class="cmp-title">Détail par source</span>
      <button class="cmp-close" onclick="toggleCmp()">✕</button>
    </div>
    <div class="cmp-scroll">
      <div id="cmpContent" style="padding:1.25rem 1.5rem;color:var(--text2);font-size:.875rem">Chargement…</div>
    </div>
  </div>

  <footer>
    <a href="/docs" target="_blank">
      <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="2" y="2" width="12" height="12" rx="2"/><line x1="5" y1="6" x2="11" y2="6"/><line x1="5" y1="9" x2="9" y2="9"/></svg>
      Swagger
    </a>
    <a href="/sante" target="_blank">
      <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8"><polyline points="2,8 5,8 6,4 8,12 10,6 11,8 14,8"/></svg>
      Santé
    </a>
    <a href="/dashboard" target="_blank">
      <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="2" y="9" width="3" height="5" rx="1"/><rect x="6.5" y="6" width="3" height="8" rx="1"/><rect x="11" y="2" width="3" height="12" rx="1"/></svg>
      Dashboard
    </a>
    <a href="http://localhost:3000" target="_blank">
      <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="8" cy="8" r="6"/><polyline points="8,4 8,8 11,10"/></svg>
      Grafana
    </a>
    <a href="/metrics" target="_blank">
      <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8"><polyline points="2,12 5,7 8,9 11,4 14,7"/></svg>
      Prometheus
    </a>
  </footer>
</div>

<script>
'use strict';
let ws=null,liveOn=false,cmpOn=false,lastVille='',lastPays='';
const $=id=>document.getElementById(id);
const R=document.documentElement;

/* THEME */
const theme=localStorage.getItem('th')||'dark';
R.dataset.theme=theme;
updateThemeLabel(theme);
$('themeBtn').addEventListener('click',()=>{
  const t=R.dataset.theme==='dark'?'light':'dark';
  R.dataset.theme=t;
  localStorage.setItem('th',t);
  updateThemeLabel(t);
});
function updateThemeLabel(t){$('themeLabel').textContent=t==='dark'?'🌙':'☀️'}

/* WEATHER ICONS */
const ICON_MAP=[
  [/orage|thunder|storm/,'⛈️'],[/neige|snow|blizzard/,'❄️'],[/grêle|hail/,'🌨️'],
  [/pluie|rain|averse|drizzle/,'🌧️'],[/brouillard|fog|brume|mist/,'🌫️'],
  [/nuageux|couvert|cloud|overcast/,'☁️'],[/partiellement|partly|éclaircies/,'⛅'],
  [/dégagé|ensoleillé|clear|sunny|soleil/,'☀️'],
];
function getIcon(d){
  const s=(d||'').toLowerCase();
  for(const[r,e] of ICON_MAP) if(r.test(s)) return e;
  return '🌤';
}

/* BACKGROUND GRADIENT per condition */
function getBg(d){
  const s=(d||'').toLowerCase();
  if(/orage|thunder|storm/.test(s)) return 'linear-gradient(135deg,#0d1117,#1a1f2e)';
  if(/neige|snow/.test(s)) return 'linear-gradient(135deg,#111827,#1e3a5f)';
  if(/pluie|rain|drizzle/.test(s)) return 'linear-gradient(135deg,#0d1b2a,#0e2236)';
  if(/brouillard|fog|brume/.test(s)) return 'linear-gradient(135deg,#131929,#1c2a3a)';
  if(/nuageux|couvert|cloud/.test(s)) return 'linear-gradient(135deg,#111827,#1a2540)';
  if(/dégagé|clear|sunny|ensoleillé/.test(s)) return 'linear-gradient(135deg,#0c1f4a,#0e3060)';
  return 'linear-gradient(135deg,#111827,#1a2540)';
}
function getBgLight(d){
  const s=(d||'').toLowerCase();
  if(/orage|thunder|storm/.test(s)) return 'linear-gradient(135deg,#b8c4d4,#d0dae6)';
  if(/pluie|rain|drizzle/.test(s)) return 'linear-gradient(135deg,#c4d4e8,#d8e8f4)';
  if(/nuageux|couvert|cloud/.test(s)) return 'linear-gradient(135deg,#d4dce8,#e4edf8)';
  if(/dégagé|clear|sunny|ensoleillé/.test(s)) return 'linear-gradient(135deg,#c8dcf8,#dceeff)';
  return 'linear-gradient(135deg,#d0dce8,#e4edf8)';
}

/* CONFIDENCE */
function confColor(v){return v>=80?'var(--green)':v>=50?'var(--orange)':'var(--red)'}
function confLabel(v){
  return v>=80?'Excellent accord inter-sources'
        :v>=60?'Bon accord inter-sources'
        :v>=40?'Accord modéré — légères divergences'
              :'Divergence élevée — données à vérifier';
}

/* DISPLAY */
function show(data){
  $('card').classList.add('on');
  const isDark=R.dataset.theme==='dark';
  $('cardTop').style.background=isDark?getBg(data.description):getBgLight(data.description);
  $('wEmoji').textContent=getIcon(data.description);
  $('cityName').textContent=data.ville;
  $('ctag').textContent=data.pays;
  $('cdesc').textContent=data.description||'—';
  $('temp').textContent=data.temperature_c+'°C';
  $('humidity').textContent=data.humidite_pct;
  $('wind').textContent=data.vent_kmh;
  $('nbOk').textContent=data.fournisseurs_ok?.length||'—';
  $('srcCount').textContent=(data.nb_sources||'?')+' source'+(data.nb_sources>1?'s':'')+' fusionnée'+(data.nb_sources>1?'s':'');

  const sc=data.indice_confiance??100;
  const col=confColor(sc);
  $('confNum').textContent=sc+'%';
  $('confNum').style.color=col;
  $('confFill').style.width=sc+'%';
  $('confFill').style.background=col;
  $('confLbl').textContent=confLabel(sc);

  const pills=$('pills'); pills.innerHTML='';
  (data.fournisseurs_ok||[]).forEach(f=>{
    const el=document.createElement('span');
    el.className='pill pill-ok';
    el.innerHTML='<span class="pdot"></span>'+f;
    pills.appendChild(el);
  });
  (data.fournisseurs_ko||[]).forEach(f=>{
    const el=document.createElement('span');
    el.className='pill pill-ko';
    el.innerHTML='<span class="pdot"></span>'+f;
    pills.appendChild(el);
  });

  if(data.avertissement){$('alertWarnTxt').textContent=data.avertissement;$('alertWarn').classList.add('on')}
  else $('alertWarn').classList.remove('on');

  const d=new Date(data.genere_a);
  const cache=data.depuis_cache?' <span class="chip-cache">CACHE</span>':'';
  $('ts').innerHTML=d.toLocaleString('fr-FR')+cache;
}

function err(m){$('alertErrTxt').textContent=m;$('alertErr').classList.add('on')}
function clearErr(){$('alertErr').classList.remove('on')}
function clearWarn(){$('alertWarn').classList.remove('on')}

/* SEARCH */
async function chercher(){
  const v=$('ville').value.trim();
  const p=($('pays').value.trim().toUpperCase())||'FR';
  if(!v) return;
  clearErr();clearWarn();
  $('loader').classList.add('on');
  $('btnSearch').disabled=true;
  try{
    const r=await fetch('/meteo?ville='+encodeURIComponent(v)+'&pays='+p);
    const d=await r.json();
    if(!r.ok){err(d.detail?.erreur||'Erreur '+r.status);return}
    show(d);lastVille=v;lastPays=p;
    if(cmpOn) loadCmp(v,p);
  }catch(e){err('Connexion impossible')}
  finally{$('loader').classList.remove('on');$('btnSearch').disabled=false}
}

/* COMPARE */
function toggleCmp(){
  cmpOn=!cmpOn;
  $('cmpPanel').classList.toggle('on',cmpOn);
  $('btnCmp').classList.toggle('on-compare',cmpOn);
  if(cmpOn&&lastVille) loadCmp(lastVille,lastPays||'FR');
}
async function loadCmp(v,p){
  $('cmpContent').innerHTML='<span style="color:var(--text2)">Chargement…</span>';
  const old=$('cmpPanel').querySelector('.cmp-footer');
  if(old) old.remove();
  try{
    const r=await fetch('/comparer?ville='+encodeURIComponent(v)+'&pays='+p);
    const d=await r.json();
    if(!r.ok){$('cmpContent').innerHTML='<span style="color:var(--red)">Erreur</span>';return}
    renderCmp(d);
  }catch{$('cmpContent').innerHTML='<span style="color:var(--red)">Erreur réseau</span>'}
}
function renderCmp(d){
  const PL={openweather:'OpenWeather',open_meteo:'Open-Meteo',weatherapi:'WeatherAPI'};
  let rows='';
  ['openweather','open_meteo','weatherapi'].forEach(p=>{
    const s=d.sources[p];
    if(s){
      rows+=`<tr>
        <td class="td-name">${PL[p]}</td>
        <td class="td-n">${s.temperature_c}°C</td>
        <td class="td-n">${s.humidite_pct}%</td>
        <td class="td-n">${s.vent_kmh}&nbsp;km/h</td>
        <td class="td-d">${s.description}</td>
        <td class="td-ok">${s.depuis_cache?'📦':'✓ Frais'}</td>
      </tr>`;
    }else{
      rows+=`<tr style="opacity:.4"><td class="td-name">${PL[p]}</td><td>—</td><td>—</td><td>—</td><td>—</td><td class="td-ko">✗</td></tr>`;
    }
  });
  const e=d.ecarts||{};
  if(Object.keys(e).length){
    rows+=`<tr class="ecart-tr"><td>Écart max</td><td>${e.temperature_c??'—'}°C</td><td>${e.humidite_pct??'—'}%</td><td>${e.vent_kmh??'—'}&nbsp;km/h</td><td colspan="2"></td></tr>`;
  }
  $('cmpContent').innerHTML=`<table><thead><tr>
    <th>Fournisseur</th><th>Temp.</th><th>Humidité</th><th>Vent</th><th>Conditions</th><th>Statut</th>
  </tr></thead><tbody>${rows}</tbody></table>`;

  const sc=d.indice_consensus;
  const col=confColor(sc);
  const footer=document.createElement('div');
  footer.className='cmp-footer';
  footer.innerHTML=`
    <div class="csc" style="color:${col}">${sc}%</div>
    <div class="csc-wrap">
      <div class="track"><div class="fill" style="width:${sc}%;background:${col}"></div></div>
      <div class="csc-sub">${confLabel(sc)} · ${new Date(d.genere_a).toLocaleString('fr-FR')}</div>
    </div>`;
  $('cmpPanel').appendChild(footer);
}

/* LIVE WS */
function toggleLive(){liveOn?stopLive():startLive()}
function startLive(){
  const v=$('ville').value.trim(),p=($('pays').value.trim().toUpperCase())||'FR';
  if(!v) return;
  const proto=location.protocol==='https:'?'wss':'ws';
  ws=new WebSocket(`${proto}://${location.host}/ws/meteo/${encodeURIComponent(v)}?pays=${p}&interval=30`);
  ws.onmessage=e=>{
    const d=JSON.parse(e.data);
    if(d.erreur) err('Live : '+d.erreur);
    else{clearErr();show(d);if(cmpOn)loadCmp(v,p)}
  };
  ws.onclose=()=>{if(liveOn)stopLive()};
  liveOn=true;lastVille=v;lastPays=p;
  $('liveChip').classList.add('on');
  $('btnLive').classList.add('on-live');
  $('btnLive').childNodes[$('btnLive').childNodes.length-1].textContent=' Arrêter le live';
}
function stopLive(){
  ws?.close();ws=null;liveOn=false;
  $('liveChip').classList.remove('on');
  $('btnLive').classList.remove('on-live');
  $('btnLive').childNodes[$('btnLive').childNodes.length-1].textContent=' Flux live (30s)';
}

/* Re-apply gradient on theme change */
document.getElementById('themeBtn').addEventListener('click',()=>{
  const desc=$('cdesc').textContent;
  if($('card').classList.contains('on')){
    $('cardTop').style.background=R.dataset.theme==='dark'?getBg(desc):getBgLight(desc);
  }
});

chercher();
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


# ============================================================
# LANCEMENT DIRECT (développement)
# ============================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)