'use strict';

const API  = window.location.origin;
const DAYS = ['Dim','Lun','Mar','Mer','Jeu','Ven','Sam'];
const FLAGS = {
  TG:'🇹🇬',GH:'🇬🇭',CI:'🇨🇮',BJ:'🇧🇯',NG:'🇳🇬',SN:'🇸🇳',ML:'🇲🇱',BF:'🇧🇫',
  NE:'🇳🇪',GN:'🇬🇳',SL:'🇸🇱',CM:'🇨🇲',CD:'🇨🇩',KE:'🇰🇪',ET:'🇪🇹',
  FR:'🇫🇷',US:'🇺🇸',AE:'🇦🇪',GB:'🇬🇧',EG:'🇪🇬',
  DE:'🇩🇪',ES:'🇪🇸',IT:'🇮🇹',JP:'🇯🇵',BR:'🇧🇷',AU:'🇦🇺',RU:'🇷🇺',
  IN:'🇮🇳',CN:'🇨🇳',ZA:'🇿🇦',MX:'🇲🇽',CA:'🇨🇦',AR:'🇦🇷',MA:'🇲🇦',
};
const CITIES = [
  {v:'Lomé',        p:'TG', lat:6.137,   lng:1.212},
  {v:'Accra',       p:'GH', lat:5.560,   lng:-0.197},
  {v:'Abidjan',     p:'CI', lat:5.359,   lng:-4.009},
  {v:'Cotonou',     p:'BJ', lat:6.366,   lng:2.419},
  {v:'Lagos',       p:'NG', lat:6.524,   lng:3.379},
  {v:'Dakar',       p:'SN', lat:14.693,  lng:-17.447},
  {v:'Bamako',      p:'ML', lat:12.653,  lng:-7.951},
  {v:'Ouagadougou', p:'BF', lat:12.366,  lng:-1.534},
  {v:'Niamey',      p:'NE', lat:13.514,  lng:2.113},
  {v:'Conakry',     p:'GN', lat:9.537,   lng:-13.677},
  {v:'Freetown',    p:'SL', lat:8.484,   lng:-13.229},
  {v:'Yaoundé',     p:'CM', lat:3.866,   lng:11.517},
  {v:'Kinshasa',    p:'CD', lat:-4.322,  lng:15.322},
  {v:'Nairobi',     p:'KE', lat:-1.286,  lng:36.817},
  {v:'Addis-Abeba', p:'ET', lat:9.025,   lng:38.747},
  {v:'Paris',       p:'FR', lat:48.857,  lng:2.352},
  {v:'New York',    p:'US', lat:40.713,  lng:-74.006},
  {v:'Dubai',       p:'AE', lat:25.205,  lng:55.271},
  {v:'Londres',     p:'GB', lat:51.507,  lng:-0.128},
  {v:'Le Caire',    p:'EG', lat:30.044,  lng:31.236},
];

let map, tiles;
let mkrs     = {};
let cur      = null;
let tab      = 'donnees';
let acTm     = null;
let ws       = null;
let wsLive   = null;
let wsSysData = null;

const $ = id => document.getElementById(id);
const fl = c => FLAGS[c] || '🌐';

function esc(s) {
  const d = document.createElement('div');
  d.textContent = (s == null) ? '' : String(s);
  return d.innerHTML;
}

function tColor(t) {
  if (t >= 40) return '#dc2626';
  if (t >= 35) return '#f97316';
  if (t >= 30) return '#fb923c';
  if (t >= 25) return '#fbbf24';
  if (t >= 20) return '#a3e635';
  if (t >= 15) return '#34d399';
  if (t >= 10) return '#38bdf8';
  if (t >=  0) return '#93c5fd';
  return '#bfdbfe';
}

function fmt(iso) {
  return new Date(iso).toLocaleString('fr-FR', {
    day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'
  });
}

// Icônes météo SVG
function wSvg(desc, px) {
  px = px || 48;
  const d   = (desc || '').toLowerCase();
  const sw  = (px / 28).toFixed(1);
  const att = `width="${px}" height="${px}" viewBox="0 0 48 48" fill="none" stroke-linecap="round" stroke-linejoin="round"`;

  if (d.includes('orage') || d.includes('tonnerre')) {
    return `<svg ${att}><path d="M36 14a11 11 0 0 1 0 22H14A10 10 0 1 1 24.3 18a11 11 0 0 1 11.7-4Z" stroke="var(--muted2)" stroke-width="${sw}"/><polyline points="23,33 19.5,41 26.5,41 23,49" stroke="var(--warn)" stroke-width="${(sw*1.1).toFixed(1)}"/></svg>`;
  }
  if (d.includes('neige') || d.includes('grêle')) {
    return `<svg ${att}><path d="M36 14a11 11 0 0 1 0 22H14A10 10 0 1 1 24.3 18a11 11 0 0 1 11.7-4Z" stroke="var(--muted2)" stroke-width="${sw}"/><line x1="16" y1="38" x2="16" y2="43" stroke="var(--cold)" stroke-width="${sw}"/><line x1="24" y1="38" x2="24" y2="43" stroke="var(--cold)" stroke-width="${sw}"/><line x1="32" y1="38" x2="32" y2="43" stroke="var(--cold)" stroke-width="${sw}"/><circle cx="16" cy="44.5" r="2" fill="var(--cold)"/><circle cx="24" cy="44.5" r="2" fill="var(--cold)"/><circle cx="32" cy="44.5" r="2" fill="var(--cold)"/></svg>`;
  }
  if (d.includes('pluie') || d.includes('averse') || d.includes('bruine')) {
    return `<svg ${att}><path d="M36 14a11 11 0 0 1 0 22H14A10 10 0 1 1 24.3 18a11 11 0 0 1 11.7-4Z" stroke="var(--muted2)" stroke-width="${sw}"/><line x1="16" y1="39" x2="14" y2="45" stroke="var(--cold)" stroke-width="${sw}"/><line x1="24" y1="39" x2="22" y2="45" stroke="var(--cold)" stroke-width="${sw}"/><line x1="32" y1="39" x2="30" y2="45" stroke="var(--cold)" stroke-width="${sw}"/></svg>`;
  }
  if (d.includes('brouillard') || d.includes('brume')) {
    return `<svg ${att}><circle cx="24" cy="17" r="8" stroke="var(--warn)" stroke-width="${sw}"/><line x1="10" y1="26" x2="38" y2="26" stroke="var(--muted2)" stroke-width="${sw}"/><line x1="8"  y1="32" x2="40" y2="32" stroke="var(--muted2)" stroke-width="${sw}"/><line x1="12" y1="38" x2="36" y2="38" stroke="var(--muted2)" stroke-width="${sw}"/></svg>`;
  }
  if ((d.includes('nuage') || d.includes('couvert') || d.includes('variable')) && (d.includes('soleil') || d.includes('ensoleill') || d.includes('partiell'))) {
    return `<svg ${att}><circle cx="17" cy="20" r="8" stroke="var(--warn)" stroke-width="${sw}"/><path d="M32 24a9 9 0 0 1 0 18H18a8 8 0 1 1 4.3-14.7A9 9 0 0 1 32 24Z" stroke="var(--muted2)" stroke-width="${sw}"/></svg>`;
  }
  if (d.includes('nuage') || d.includes('couvert') || d.includes('variable')) {
    return `<svg ${att}><path d="M36 14a11 11 0 0 1 0 22H14A10 10 0 1 1 24.3 18a11 11 0 0 1 11.7-4Z" stroke="var(--muted2)" stroke-width="${sw}"/></svg>`;
  }
  if (d.includes('dégag') || d.includes('soleil') || d.includes('ensoleill') || d.includes('clair')) {
    return `<svg ${att}><circle cx="24" cy="24" r="9" stroke="var(--warn)" stroke-width="${sw}"/><line x1="24" y1="7" x2="24" y2="11" stroke="var(--warn)" stroke-width="${sw}"/><line x1="24" y1="37" x2="24" y2="41" stroke="var(--warn)" stroke-width="${sw}"/><line x1="7" y1="24" x2="11" y2="24" stroke="var(--warn)" stroke-width="${sw}"/><line x1="37" y1="24" x2="41" y2="24" stroke="var(--warn)" stroke-width="${sw}"/><line x1="11.7" y1="11.7" x2="14.5" y2="14.5" stroke="var(--warn)" stroke-width="${sw}"/><line x1="33.5" y1="33.5" x2="36.3" y2="36.3" stroke="var(--warn)" stroke-width="${sw}"/><line x1="36.3" y1="11.7" x2="33.5" y2="14.5" stroke="var(--warn)" stroke-width="${sw}"/><line x1="14.5" y1="33.5" x2="11.7" y2="36.3" stroke="var(--warn)" stroke-width="${sw}"/></svg>`;
  }
  // défaut thermomètre
  return `<svg ${att}><circle cx="24" cy="36" r="7" stroke="var(--accent)" stroke-width="${sw}"/><rect x="21" y="10" width="6" height="20" rx="3" stroke="var(--accent)" stroke-width="${sw}"/></svg>`;
}

// Icone compacte SVG pour les marqueurs (14x14)
function wIcoSm(desc, col) {
  const d = (desc || '').toLowerCase();
  if (d.includes('orage'))
    return `<svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke-linecap="round"><path d="M10.5 4a3.5 3.5 0 0 1 0 7H3.5A3 3 0 1 1 7 5a3.5 3.5 0 0 1 3.5-1Z" stroke="${col}" stroke-width="1.3"/><polyline points="6.5,8 5,11 8,11 6.5,14" stroke="var(--warn)" stroke-width="1.3"/></svg>`;
  if (d.includes('neige'))
    return `<svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke-linecap="round"><path d="M10.5 4a3.5 3.5 0 0 1 0 7H3.5A3 3 0 1 1 7 5a3.5 3.5 0 0 1 3.5-1Z" stroke="${col}" stroke-width="1.3"/><circle cx="4.5" cy="12" r=".8" fill="${col}"/><circle cx="7" cy="12" r=".8" fill="${col}"/><circle cx="9.5" cy="12" r=".8" fill="${col}"/></svg>`;
  if (d.includes('pluie') || d.includes('averse'))
    return `<svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke-linecap="round"><path d="M10.5 4a3.5 3.5 0 0 1 0 7H3.5A3 3 0 1 1 7 5a3.5 3.5 0 0 1 3.5-1Z" stroke="${col}" stroke-width="1.3"/><line x1="4.5" y1="11" x2="3.8" y2="13" stroke="${col}" stroke-width="1.3"/><line x1="7" y1="11" x2="6.3" y2="13" stroke="${col}" stroke-width="1.3"/><line x1="9.5" y1="11" x2="8.8" y2="13" stroke="${col}" stroke-width="1.3"/></svg>`;
  if (d.includes('nuage') || d.includes('couvert'))
    return `<svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke-linecap="round"><path d="M10.5 4a3.5 3.5 0 0 1 0 7H3.5A3 3 0 1 1 7 5a3.5 3.5 0 0 1 3.5-1Z" stroke="${col}" stroke-width="1.3"/></svg>`;
  // soleil
  return `<svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="${col}" stroke-width="1.3" stroke-linecap="round"><circle cx="7" cy="7" r="2.8"/><line x1="7" y1="1" x2="7" y2="2.5"/><line x1="7" y1="11.5" x2="7" y2="13"/><line x1="1" y1="7" x2="2.5" y2="7"/><line x1="11.5" y1="7" x2="13" y2="7"/></svg>`;
}

function confGauge(pct) {
  const len  = 84.8;
  const fill = (pct / 100) * len;
  const col  = pct >= 80 ? 'var(--ok)' : pct >= 60 ? 'var(--accent)' : pct >= 40 ? 'var(--warn)' : 'var(--err)';
  const lbl  = pct >= 80 ? 'Excellent' : pct >= 60 ? 'Bon' : pct >= 40 ? 'Faible' : 'Critique';
  return `<div class="gauge-wrap">
    <svg width="80" height="46" viewBox="0 0 80 46" fill="none">
      <path d="M13,36 A27,27 0 0,1 67,36" stroke="var(--border)" stroke-width="7" stroke-linecap="round"/>
      <path d="M13,36 A27,27 0 0,1 67,36" stroke="${col}" stroke-width="7" stroke-linecap="round" stroke-dasharray="${fill.toFixed(1)} ${len}"/>
      <text x="40" y="29" text-anchor="middle" font-size="12" font-weight="900" fill="${col}" font-family="system-ui,sans-serif">${pct}</text>
    </svg>
    <div class="gauge-lbl">Confiance — ${lbl}</div>
  </div>`;
}

function pipeHtml(d) {
  const steps = [
    {n:1, name:'Réception de la requête', desc:'Validation Pydantic — ville, pays (regex anti-injection)'},
    {n:2, name:'Cache L2 (Redis, 1 h)', desc:'Lecture clé meteo:l2:… — retour immédiat si disponible'},
    {n:3, name:'Cache L1 (5 min / fournisseur)', desc:'Lecture meteo:l1:… — fusion partielle si ≥ 1 provider en cache'},
    {n:4, name:'Appels parallèles (asyncio.gather)', desc:'3 APIs en simultané, chacune gardée par son Circuit Breaker'},
    {n:5, name:'Fusion et stockage', desc:'Calcul indice de confiance, écriture L1 + L2, retour MeteoConsolidée'},
  ];
  const active = d.depuis_cache ? [1, 2] : [1, 4, 5];
  return steps.map(s =>
    `<div class="pipe-step${active.includes(s.n) ? ' active' : ''}">
      <div class="pipe-num">${s.n}</div>
      <div class="pipe-info">
        <div class="pipe-name">${s.name}</div>
        <div class="pipe-desc">${s.desc}</div>
      </div>
    </div>`
  ).join('');
}

function updateCacheBadge(d) {
  const el = $('phCache');
  el.className = 'ph-cache';
  void el.offsetWidth;
  if (d.depuis_cache) {
    const niv = (d.cache_niveau || 'L2').toUpperCase();
    el.textContent   = `Cache ${niv}`;
    el.classList.add(niv.toLowerCase());
    el.style.display = 'inline-block';
  } else {
    el.style.display = 'none';
  }
}

function updateCbPastilles() {
  const el = $('phCb');
  if (!el) return;
  const cb = wsSysData && wsSysData.circuit_breakers;
  if (!cb) { el.innerHTML = ''; return; }
  const providers = {openweather:'OWeather', open_meteo:'OpenMét.', weatherapi:'WthAPI'};
  el.innerHTML = Object.entries(providers).map(([k, abbr]) => {
    const st  = cb[k] || 'CLOSED';
    const cls = st === 'CLOSED' ? 'cb-closed' : st === 'HALF_OPEN' ? 'cb-half' : 'cb-open';
    return `<span class="cb-dot ${cls}"></span><span class="cb-lbl">${abbr}</span>`;
  }).join('');
}

function toggleTheme() {
  const dark = document.documentElement.dataset.theme === 'dark';
  document.documentElement.dataset.theme = dark ? 'light' : 'dark';
  const ico = $('themeIco');
  if (dark) {
    ico.innerHTML = '<path d="M12 3a6 6 0 0 0 0 10 6 6 0 0 1 0-10Z" fill="currentColor" stroke="none"/>';
  } else {
    ico.innerHTML = '<circle cx="8" cy="8" r="3.2"/><line x1="8" y1="1" x2="8" y2="2.5"/><line x1="8" y1="13.5" x2="8" y2="15"/><line x1="1" y1="8" x2="2.5" y2="8"/><line x1="13.5" y1="8" x2="15" y2="8"/><line x1="3.1" y1="3.1" x2="4.1" y2="4.1"/><line x1="11.9" y1="11.9" x2="12.9" y2="12.9"/><line x1="12.9" y1="3.1" x2="11.9" y2="4.1"/><line x1="4.1" y1="11.9" x2="3.1" y2="12.9"/>';
  }
  updateTiles();
}

function updateTiles() {
  const dark = document.documentElement.dataset.theme === 'dark';
  if (tiles) map.removeLayer(tiles);
  tiles = L.tileLayer(
    dark
      ? 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'
      : 'https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png',
    {
      attribution:'© <a href="https://carto.com">CartoDB</a> © <a href="https://openstreetmap.org">OpenStreetMap</a>',
      subdomains:'abcd', maxZoom:19,
    }
  ).addTo(map);
}

function initMap() {
  map = L.map('map', {zoomControl:false, attributionControl:true}).setView([10, 8], 3);
  L.control.zoom({position:'bottomright'}).addTo(map);
  updateTiles();

  // Marqueurs de chargement
  CITIES.forEach(c => {
    const html = `<div class="mk"><div class="mk-ring" style="border-color:var(--muted)"><div class="spin" style="width:11px;height:11px;border-width:1.5px"></div></div><div class="mk-n">${esc(c.v)}</div></div>`;
    const icon = L.divIcon({html, className:'', iconSize:[56,48], iconAnchor:[28,20]});
    const m    = L.marker([c.lat, c.lng], {icon}).addTo(map);
    mkrs[c.v]  = {m, halo:null, data:null};
  });

  // Charger météo de chaque ville
  CITIES.forEach(c => loadCity(c));

  // Clic sur la carte → reverse geocoding
  map.on('click', e => {
    ripple(e.originalEvent.clientX, e.originalEvent.clientY);
    reverseAndFetch(e.latlng.lat, e.latlng.lng);
  });
}

async function loadCity(c) {
  try {
    const r = await fetch(`${API}/meteo?ville=${encodeURIComponent(c.v)}&pays=${encodeURIComponent(c.p)}`);
    if (!r.ok) return;
    const d = await r.json();
    setMarker(c, d);
  } catch { /* silencieux */ }
}

function setMarker(c, d) {
  const t   = d.temperature_c;
  const col = tColor(t);

  if (mkrs[c.v] && mkrs[c.v].halo) map.removeLayer(mkrs[c.v].halo);
  const halo = L.circle([c.lat, c.lng], {
    radius:200000, color:'transparent', fillColor:col, fillOpacity:.07, interactive:false,
  }).addTo(map);

  const html = `<div class="mk"><div class="mk-ring" style="border-color:${col};box-shadow:0 0 14px ${col}22">${wIcoSm(d.description,col)}<span class="mk-t" style="color:${col}">${t.toFixed(0)}°</span></div><div class="mk-n">${esc(c.v)}</div></div>`;
  const icon = L.divIcon({html, className:'', iconSize:[56,48], iconAnchor:[28,20]});

  mkrs[c.v].m.setIcon(icon);
  mkrs[c.v].halo = halo;
  mkrs[c.v].data = d;

  mkrs[c.v].m.off('click').on('click', ev => {
    L.DomEvent.stopPropagation(ev);
    openPanel(c.v, c.p, d, c.lat, c.lng);
  });

  mkrs[c.v].m.bindTooltip(
    `<div class="wt-n">${esc(c.v)} ${fl(c.p)}</div><div class="wt-t" style="color:${col}">${t.toFixed(1)} °C</div><div class="wt-d">${esc(d.description)}</div>`,
    {direction:'top', className:'wtip', offset:[0,-22], sticky:false}
  );
}

// Reverse geocoding sur click carte libre
async function reverseAndFetch(lat, lng) {
  showToast('Recherche en cours…', 'info');
  try {
    const r = await fetch(
      `https://nominatim.openstreetmap.org/reverse?lat=${lat.toFixed(5)}&lon=${lng.toFixed(5)}&format=json&accept-language=fr&zoom=10`,
      {headers: {'Accept': 'application/json'}}
    );
    if (!r.ok) { showToast('Zone non disponible', 'warn'); return; }
    const d    = await r.json();
    const addr = d.address || {};
    const ville = addr.city || addr.town || addr.village || addr.municipality || addr.county || addr.state;
    const pays  = (addr.country_code || 'fr').toUpperCase();
    if (ville) { hideToast(); fetchAndOpen(ville, pays, lat, lng); }
    else showToast('Aucune ville trouvée ici', 'warn');
  } catch { showToast('Erreur réseau', 'warn'); }
}

function ripple(x, y) {
  const el = document.createElement('div');
  el.className = 'click-ripple';
  el.style.cssText = `left:${x}px;top:${y}px`;
  document.body.appendChild(el);
  el.addEventListener('animationend', () => el.remove(), {once:true});
}

function openPanel(ville, pays, d, lat, lng) {
  if (cur && cur.name !== ville && wsLive) {
    wsLive.close(); wsLive = null;
    $('liveBtn').classList.remove('live-on');
    $('liveBtn').textContent = 'Direct';
    $('phLive').classList.remove('show');
  }
  cur = {name:ville, code:pays, data:d, lat, lng};
  tab = 'donnees';

  $('phCity').textContent = ville;
  $('phMeta').textContent = `${fl(pays)} ${pays}`;
  updateCacheBadge(d);
  updateCbPastilles();

  const col = tColor(d.temperature_c);
  $('pTemp').innerHTML    = `<span style="color:${col}">${d.temperature_c.toFixed(1)}</span><sup>°C</sup>`;
  $('pCond').textContent  = d.description;
  $('pCond').style.color  = col;
  $('pFeels').textContent = d.ressenti_c != null ? `Ressenti ${d.ressenti_c.toFixed(1)} °C` : '';
  $('pIco').innerHTML     = wSvg(d.description, 58);

  document.querySelectorAll('.ptab').forEach(b => b.classList.remove('on'));
  document.querySelector('[data-pt="donnees"]').classList.add('on');
  showData(d);
  $('panel').classList.add('open');
}

function closePanel() {
  $('panel').classList.remove('open');
  if (wsLive) { wsLive.close(); wsLive = null; }
  $('liveBtn').classList.remove('live-on');
  $('liveBtn').textContent = 'Direct';
  $('phLive').classList.remove('show');
}

function ptSwitch(nom) {
  tab = nom;
  document.querySelectorAll('.ptab').forEach(b => b.classList.remove('on'));
  document.querySelector(`[data-pt="${nom}"]`).classList.add('on');
  if (!cur) return;
  if (nom === 'donnees')    showData(cur.data);
  if (nom === 'previsions') loadForecast();
  if (nom === 'historique') loadHistory();
  if (nom === 'sources')    loadSources();
}

function showData(d) {
  const all    = ['openweather', 'open_meteo', 'weatherapi'];
  const ok     = d.fournisseurs_ok || [];
  const pNames = {openweather:'OpenWeather', open_meteo:'Open-Météo', weatherapi:'WeatherAPI'};
  $('pbody').innerHTML =
    confGauge(d.indice_confiance || 0) +
    `<div class="sg">
       ${sc(d.humidite_pct.toFixed(0) + ' %', 'Humidité')}
       ${sc(d.vent_kmh.toFixed(0) + ' km/h', 'Vent')}
       ${sc(d.nb_sources + ' / 3', 'Sources actives')}
       ${sc(d.depuis_cache ? 'Cache' : 'Direct', 'Origine')}
     </div>
     <div class="sec-hd">Fournisseurs</div>
     ${all.map(s => `<div class="src-row"><span class="src-name">${esc(pNames[s] || s)}</span>${ok.includes(s) ? '<span class="src-ok">Actif</span>' : '<span class="src-ko">Indisponible</span>'}</div>`).join('')}
     <div class="p-time">Généré le ${fmt(d.genere_a)}</div>
     <div class="p-actions">
       <button class="btn-sm" data-fn="ptSwitch" data-a1="previsions">Prévisions 7 j</button>
       <button class="btn-sm" data-fn="ptSwitch" data-a1="sources">Comparer sources</button>
       <button class="btn-sm" data-fn="doExport" data-a1="csv">CSV</button>
       <button class="btn-sm" data-fn="doExport" data-a1="json">JSON</button>
     </div>
     <div class="pipe-section" id="pipeSection">
       <div class="pipe-hd-toggle" data-fn="pipePick">
         <span class="pipe-hd-toggle-lbl">Pipeline de traitement</span>
         <span class="pipe-hd-toggle-arr">▼</span>
       </div>
       <div class="pipe-content">${pipeHtml(d)}</div>
     </div>`;
}

function sc(v, l) {
  return `<div class="sc"><div class="sc-v">${esc(v)}</div><div class="sc-l">${esc(l)}</div></div>`;
}

async function loadForecast() {
  if (!cur) return;
  $('pbody').innerHTML = `<div class="loading"><div class="spin"></div>Chargement…</div>`;
  try {
    const r = await fetch(`${API}/previsions?ville=${encodeURIComponent(cur.name)}&pays=${encodeURIComponent(cur.code)}&jours=7`);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    $('pbody').innerHTML =
      `<div class="sec-hd">7 prochains jours</div>` +
      d.jours.map(j => {
        const dt   = new Date(j.date + 'T12:00:00');
        const cmax = tColor(j.temp_max), cmin = tColor(j.temp_min);
        const rain = j.precipitation_mm > 0.3 ? `<span class="f-rain">${j.precipitation_mm} mm</span>` : '';
        return `<div class="f-row">
          <span class="f-day">${DAYS[dt.getDay()]}</span>
          <span class="f-ico">${wSvg(j.description, 18)}</span>
          <span class="f-cond">${esc(j.description)}</span>
          <span class="f-range"><span style="color:${cmax}">${j.temp_max.toFixed(0)}°</span>&thinsp;/&thinsp;<span style="color:${cmin}">${j.temp_min.toFixed(0)}°</span></span>
          ${rain}
        </div>`;
      }).join('');
  } catch(e) {
    $('pbody').innerHTML = `<div class="empty"><div class="empty-ico">—</div>${esc(e.message)}</div>`;
  }
}

async function loadHistory() {
  if (!cur) return;
  $('pbody').innerHTML = `<div class="loading"><div class="spin"></div>Chargement…</div>`;
  try {
    const r = await fetch(`${API}/historique?ville=${encodeURIComponent(cur.name)}&pays=${encodeURIComponent(cur.code)}&n=24`);
    if (r.status === 404) {
      $('pbody').innerHTML = `<div class="empty"><div class="empty-ico">—</div>Aucun relevé enregistré.<br>Consultez d'abord cette ville.</div>`;
      return;
    }
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    const tmap = {
      hausse: ['Tendance à la hausse', 'pill-up'],
      baisse: ['Tendance à la baisse', 'pill-dn'],
      stable: ['Température stable',   'pill-eq'],
    };
    const [label, cls] = tmap[d.tendance] || tmap.stable;
    $('pbody').innerHTML =
      `<span class="pill ${cls}">${label}</span>
       <div class="kpis">
         <div class="kpi"><div class="kpi-v">${d.temp_min != null ? d.temp_min + '°' : '—'}</div><div class="kpi-l">Min</div></div>
         <div class="kpi"><div class="kpi-v">${d.temp_max != null ? d.temp_max + '°' : '—'}</div><div class="kpi-l">Max</div></div>
         <div class="kpi"><div class="kpi-v">${d.nb_entrees}</div><div class="kpi-l">Relevés</div></div>
       </div>
       <canvas id="spark"></canvas>
       <div class="sec-hd">Derniers relevés</div>
       <table class="h-table">
         <thead><tr><th>Date</th><th>Temp.</th><th>Hum.</th><th>Vent</th></tr></thead>
         <tbody>
           ${[...d.entrees].reverse().map(e =>
             `<tr>
               <td>${esc(fmt(e.timestamp))}</td>
               <td style="font-weight:700;color:${tColor(e.temperature_c)}">${e.temperature_c.toFixed(1)} °C</td>
               <td>${e.humidite_pct.toFixed(0)} %</td>
               <td>${e.vent_kmh.toFixed(0)} km/h</td>
             </tr>`
           ).join('')}
         </tbody>
       </table>`;
    drawSpark(d.entrees.map(e => e.temperature_c));
  } catch(e) {
    $('pbody').innerHTML = `<div class="empty"><div class="empty-ico">—</div>${esc(e.message)}</div>`;
  }
}

function drawSpark(vals) {
  const cv = $('spark');
  if (!cv || vals.length < 2) return;
  const ctx = cv.getContext('2d');
  cv.width  = cv.offsetWidth || 320;
  cv.height = 86;
  const W = cv.width, H = cv.height, P = 12;
  const mn  = Math.min(...vals), mx = Math.max(...vals), rng = mx - mn || 1;
  const px  = i => P + (i / (vals.length - 1)) * (W - 2*P);
  const py  = v => H - P - ((v - mn) / rng) * (H - 2*P);
  ctx.clearRect(0, 0, W, H);
  const g = ctx.createLinearGradient(0, 0, 0, H);
  g.addColorStop(0, 'rgba(0,196,167,.18)');
  g.addColorStop(1, 'rgba(0,196,167,0)');
  ctx.beginPath();
  ctx.moveTo(px(0), H);
  vals.forEach((v, i) => ctx.lineTo(px(i), py(v)));
  ctx.lineTo(px(vals.length - 1), H);
  ctx.closePath();
  ctx.fillStyle = g;
  ctx.fill();
  ctx.beginPath();
  vals.forEach((v, i) => (i === 0 ? ctx.moveTo : ctx.lineTo).call(ctx, px(i), py(v)));
  ctx.strokeStyle = '#00c4a7';
  ctx.lineWidth   = 1.8;
  ctx.lineJoin    = 'round';
  ctx.stroke();
  [{i:vals.indexOf(mx), v:mx, c:'#f97316'}, {i:vals.indexOf(mn), v:mn, c:'#38bdf8'}].forEach(pt => {
    ctx.beginPath();
    ctx.arc(px(pt.i), py(pt.v), 3.5, 0, 2*Math.PI);
    ctx.fillStyle = pt.c;
    ctx.fill();
    ctx.font      = 'bold 9px system-ui';
    ctx.fillStyle = pt.c;
    ctx.fillText(pt.v.toFixed(1) + '°', px(pt.i) + 5, py(pt.v) - 4);
  });
}

async function loadSources() {
  if (!cur) return;
  $('pbody').innerHTML = `<div class="loading"><div class="spin"></div>Comparaison en cours…</div>`;
  try {
    const r = await fetch(`${API}/comparer?ville=${encodeURIComponent(cur.name)}&pays=${encodeURIComponent(cur.code)}`);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    const pOrder = ['openweather', 'open_meteo', 'weatherapi'];
    const pAbbr  = ['OWeather', 'Open-Météo', 'WthAPI'];

    const ecCls = (v, ok, warn) => Math.abs(v) <= ok ? 'ecart-ok' : Math.abs(v) <= warn ? 'ecart-warn' : 'ecart-bad';

    const tdRow = (label, key, unit, dec) => {
      const cells = pOrder.map(p => {
        const s = d.sources[p];
        return s != null ? `<td>${s[key].toFixed(dec)} ${unit}</td>` : `<td><span class="src-miss">—</span></td>`;
      });
      return `<tr><td>${label}</td>${cells.join('')}</tr>`;
    };

    const cs = d.indice_consensus;
    const cCol = cs >= 80 ? 'var(--ok)' : cs >= 60 ? 'var(--accent)' : cs >= 40 ? 'var(--warn)' : 'var(--err)';
    const cTxt = cs >= 80 ? 'Sources en accord — données fiables.'
      : cs >= 60 ? 'Accord modéré — légères variations entre fournisseurs.'
      : cs >= 40 ? 'Divergence notable — interpréter avec prudence.'
      : 'Sources en désaccord — données incertaines.';

    $('pbody').innerHTML = `
      <div class="sec-hd">Données par fournisseur</div>
      <table class="src-table">
        <thead><tr><th>Mesure</th>${pAbbr.map(a => `<th>${esc(a)}</th>`).join('')}</tr></thead>
        <tbody>
          ${tdRow('Temp. (°C)', 'temperature_c', '°C', 1)}
          ${tdRow('Humidité (%)', 'humidite_pct', '%', 0)}
          ${tdRow('Vent (km/h)', 'vent_kmh', 'km/h', 0)}
          <tr><td>Condition</td>${pOrder.map(p => {
            const s = d.sources[p];
            return s ? `<td style="font-size:.63rem">${esc(s.description.substring(0,14))}</td>`
                     : `<td><span class="src-miss">—</span></td>`;
          }).join('')}</tr>
        </tbody>
      </table>
      <div class="sec-hd">Écarts entre sources</div>
      <div class="ecart-line"><span class="ecart-field">Température</span><span class="ecart-val ${ecCls(d.ecarts.temperature_c,1,3)}">± ${d.ecarts.temperature_c.toFixed(1)} °C</span></div>
      <div class="ecart-line"><span class="ecart-field">Humidité</span><span class="ecart-val ${ecCls(d.ecarts.humidite_pct,5,10)}">± ${d.ecarts.humidite_pct.toFixed(0)} %</span></div>
      <div class="ecart-line"><span class="ecart-field">Vent</span><span class="ecart-val ${ecCls(d.ecarts.vent_kmh,5,15)}">± ${d.ecarts.vent_kmh.toFixed(0)} km/h</span></div>
      <div class="sec-hd" style="margin-top:14px">Indice de consensus — ${cs} %</div>
      <div class="consensus-bar"><div class="consensus-fill" style="width:${cs}%;background:${cCol}"></div></div>
      <div class="consensus-phrase">${cTxt}</div>
      <div class="p-time">Généré le ${fmt(d.genere_a)}</div>`;
  } catch(e) {
    $('pbody').innerHTML = `<div class="empty"><div class="empty-ico">—</div>${esc(e.message)}</div>`;
  }
}

function doExport(format) {
  if (!cur) return;
  window.open(`${API}/export?ville=${encodeURIComponent(cur.name)}&pays=${encodeURIComponent(cur.code)}&format=${format}`);
}

const sInp = document.getElementById('sInp');
const acBox = document.getElementById('acBox');

sInp.addEventListener('input', () => {
  const q = sInp.value.trim();
  document.getElementById('sClear').style.display = q ? 'block' : 'none';
  if (q.length < 2) { closeAC(); return; }
  clearTimeout(acTm);
  acTm = setTimeout(() => geocode(q), 270);
});

sInp.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    e.preventDefault();
    const hi = acBox.querySelector('.hi');
    if (hi) pickAC(hi); else directSearch();
    return;
  }
  if (e.key === 'Escape') { closeAC(); return; }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const items = [...acBox.querySelectorAll('.ac-r')];
    if (!items.length) return;
    const ci = items.findIndex(x => x.classList.contains('hi'));
    items.forEach(x => x.classList.remove('hi'));
    const ni = (ci + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    items[ni].classList.add('hi');
  }
});

async function geocode(q) {
  try {
    const r = await fetch(
      `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(q)}&count=6&language=fr&format=json`
    );
    const d = await r.json();
    showAC(d.results || []);
  } catch { closeAC(); }
}

function showAC(res) {
  if (!res.length) { closeAC(); return; }
  acBox.innerHTML = res.map(r => {
    const adm = [r.admin1, r.admin2].filter(Boolean).join(', ');
    return `<div class="ac-r" data-name="${esc(r.name)}" data-code="${esc(r.country_code||'FR')}" data-lat="${r.latitude}" data-lng="${r.longitude}" data-fn="pickAC">
      <span>${fl(r.country_code)}</span>
      <div><div class="ac-nm">${esc(r.name)}</div>${adm ? `<div class="ac-sb">${esc(adm)}</div>` : ''}</div>
      <span class="ac-cc">${esc(r.country_code||'')}</span>
    </div>`;
  }).join('');
  acBox.classList.add('open');
}

function pickAC(el) {
  const name = el.dataset.name;
  const code = el.dataset.code;
  const lat  = parseFloat(el.dataset.lat);
  const lng  = parseFloat(el.dataset.lng);
  sInp.value = name;
  document.getElementById('sClear').style.display = 'block';
  closeAC();
  if (lat && lng) map.setView([lat, lng], Math.max(map.getZoom(), 7), {animate:true});
  fetchAndOpen(name, code, lat, lng);
}

function closeAC() { acBox.classList.remove('open'); acBox.innerHTML = ''; }

function clearSearch() {
  sInp.value = '';
  document.getElementById('sClear').style.display = 'none';
  closeAC();
  sInp.focus();
}

function directSearch() {
  const q = sInp.value.trim();
  if (!q) return;
  const parts = q.split(',');
  fetchAndOpen(parts[0].trim(), ((parts[1] || 'FR').trim().toUpperCase()).substring(0, 2));
}

// Récupère météo et ouvre le panneau
async function fetchAndOpen(ville, pays, lat, lng) {
  try {
    const r = await fetch(`${API}/meteo?ville=${encodeURIComponent(ville)}&pays=${encodeURIComponent(pays)}`);
    if (!r.ok) {
      const e = await r.json();
      alert((e.detail && e.detail.erreur) || 'Ville introuvable ou non disponible.');
      return;
    }
    const d  = await r.json();
    const lt = lat || 0, lg = lng || 0;
    // Si nouvelle ville hors liste des 20, ajouter un marqueur temporaire
    if (!mkrs[ville] && lt && lg) {
      const col  = tColor(d.temperature_c);
      const html = `<div class="mk"><div class="mk-ring" style="border-color:${col}">${wIcoSm(d.description,col)}<span class="mk-t" style="color:${col}">${d.temperature_c.toFixed(0)}°</span></div><div class="mk-n">${esc(ville)}</div></div>`;
      const icon = L.divIcon({html, className:'', iconSize:[56,48], iconAnchor:[28,20]});
      const m    = L.marker([lt, lg], {icon}).addTo(map);
      m.on('click', ev => { L.DomEvent.stopPropagation(ev); openPanel(ville, pays, d, lt, lg); });
      m.bindTooltip(
        `<div class="wt-n">${esc(ville)} ${fl(pays)}</div><div class="wt-t" style="color:${col}">${d.temperature_c.toFixed(1)} °C</div><div class="wt-d">${esc(d.description)}</div>`,
        {direction:'top', className:'wtip', offset:[0,-22]}
      );
      mkrs[ville] = {m, halo:null, data:d};
    }
    openPanel(ville, pays, d, lt, lg);
  } catch(e) { console.error(e); }
}

function geoMe() {
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition(async pos => {
    try {
      const {latitude:la, longitude:lo} = pos.coords;
      map.setView([la, lo], 9, {animate:true});
      const r = await fetch(
        `https://nominatim.openstreetmap.org/reverse?lat=${la.toFixed(5)}&lon=${lo.toFixed(5)}&format=json&accept-language=fr&zoom=10`,
        {headers: {'Accept': 'application/json'}}
      );
      const d = await r.json();
      if (d.address) {
        const addr = d.address;
        const nom  = addr.city || addr.town || addr.village || addr.municipality || addr.county || addr.state;
        const cc   = (addr.country_code || 'fr').toUpperCase();
        if (nom) { sInp.value = nom; document.getElementById('sClear').style.display = 'block'; fetchAndOpen(nom, cc, la, lo); }
      }
    } catch {}
  }, () => {});
}

function openModal(id) {
  document.getElementById('backdrop').style.display = 'block';
  if (id === 'alertes') { document.getElementById('mAlertes').classList.add('open'); loadAlertes(); }
  else if (id === 'sys') { document.getElementById('mSys').classList.add('open'); loadSys(); }
  else                   { document.getElementById('mPop').classList.add('open'); loadPop(); }
}

function closeModal(id) {
  if (id === 'alertes') document.getElementById('mAlertes').classList.remove('open');
  else if (id === 'sys') document.getElementById('mSys').classList.remove('open');
  else                   document.getElementById('mPop').classList.remove('open');
  if (!['mAlertes','mPop','mSys'].some(x => document.getElementById(x).classList.contains('open')))
    document.getElementById('backdrop').style.display = 'none';
}

function closeAll() {
  ['mAlertes','mPop','mSys'].forEach(x => document.getElementById(x).classList.remove('open'));
  document.getElementById('sysMenu').classList.remove('open');
  document.getElementById('backdrop').style.display = 'none';
}

function loadSys() {
  const el = $('bodySys');
  if (!wsSysData) {
    el.innerHTML = `<div class="loading"><div class="spin"></div>En attente des données système…</div>`;
    return;
  }
  const d      = wsSysData;
  const hits   = d.cache.hits   || 0;
  const misses = d.cache.misses || 0;
  const ratio  = d.cache.ratio_pct || 0;
  const cbMap  = d.circuit_breakers || {};
  const top    = d.top_villes || [];
  const cbLabels = {openweather:'OpenWeather', open_meteo:'Open-Météo', weatherapi:'WeatherAPI'};

  el.innerHTML = `
    <div class="sys-sec">
      <div class="sys-sec-hd">Cache Redis</div>
      <div class="cache-bar-wrap"><div class="cache-bar-fill" style="width:${ratio}%"></div></div>
      <div class="cache-nums">
        <span>${hits.toLocaleString('fr-FR')} hits</span>
        <span>${ratio.toFixed(1)} %</span>
        <span>${misses.toLocaleString('fr-FR')} misses</span>
      </div>
    </div>
    <div class="sys-sec">
      <div class="sys-sec-hd">Circuit Breakers</div>
      ${Object.entries(cbLabels).map(([k, label]) => {
        const st   = cbMap[k] || 'CLOSED';
        const cls  = st === 'CLOSED' ? 'ok' : st === 'HALF_OPEN' ? 'half' : 'err';
        const dotC = st === 'CLOSED' ? 'cb-closed' : st === 'HALF_OPEN' ? 'cb-half' : 'cb-open';
        const lbl  = st === 'CLOSED' ? 'Opérationnel' : st === 'HALF_OPEN' ? 'Test en cours…' : 'Ouvert';
        return `<div class="cb-srow">
          <div class="cb-sname">${esc(label)}</div>
          <div class="cb-sstate ${cls}">${lbl}</div>
          <span class="cb-dot ${dotC}" style="margin-left:4px"></span>
        </div>`;
      }).join('')}
    </div>
    ${top.length ? `<div class="sys-sec">
      <div class="sys-sec-hd">Top villes</div>
      ${top.slice(0,5).map(v => {
        const rc = v.rang <= 3 ? `r${v.rang}` : 'rn';
        return `<div class="pop-row" data-fn="goCity" data-a1="${esc(v.ville)}" data-a2="${esc(v.pays)}">
          <div class="rnk ${rc}">${v.rang}</div>
          <div><div class="pop-city">${fl(v.pays)} ${esc(v.ville)}</div><div class="pop-cc">${esc(v.pays)}</div></div>
          <div class="pop-nb">${v.nb_requetes.toLocaleString('fr-FR')}</div>
        </div>`;
      }).join('')}
    </div>` : ''}`;
}

async function loadAlertes() {
  document.getElementById('bodyAlertes').innerHTML = `<div class="loading"><div class="spin"></div>Chargement…</div>`;
  try {
    const r = await fetch(`${API}/alertes`);
    const d = await r.json();
    const bdg = $('bdgAlert');
    d.nb_actives > 0
      ? (bdg.textContent = d.nb_actives, bdg.classList.add('show'))
      : bdg.classList.remove('show');
    if (!d.nb_actives) {
      document.getElementById('bodyAlertes').innerHTML = `<div class="empty"><div class="empty-ico">—</div>Aucune alerte en cours</div>`;
      return;
    }
    const CM = {danger:'var(--err)', vigilance:'var(--warn)', information:'var(--cold)'};
    const UM = {canicule:'°C', gel:'°C', vent_fort:'km/h'};
    document.getElementById('bodyAlertes').innerHTML = d.alertes.map(a => {
      const col = CM[a.niveau] || 'var(--accent)';
      return `<div class="al ${esc(a.niveau)}">
        <div style="flex:1">
          <div class="al-level" style="color:${col}">${esc(a.type_alerte.replace(/_/g,' '))}</div>
          <div class="al-msg">${esc(a.message)}</div>
          <div class="al-meta">${esc(a.ville)}, ${esc(a.pays)} — ${esc(fmt(a.declenchee_a))}</div>
        </div>
        <div class="al-val">
          <div class="al-num" style="color:${col}">${esc(a.valeur.toFixed(1))}</div>
          <div class="al-unit">${esc(UM[a.type_alerte] || '')}</div>
        </div>
      </div>`;
    }).join('');
  } catch(e) {
    document.getElementById('bodyAlertes').innerHTML = `<div class="empty"><div class="empty-ico">—</div>${esc(e.message)}</div>`;
  }
}

async function loadPop() {
  document.getElementById('bodyPop').innerHTML = `<div class="loading"><div class="spin"></div>Chargement…</div>`;
  try {
    const r = await fetch(`${API}/villes-populaires?n=15`);
    const d = await r.json();
    document.getElementById('popTotal').textContent = d.total_requetes.toLocaleString('fr-FR');
    if (!d.villes.length) {
      document.getElementById('bodyPop').innerHTML = `<div class="empty"><div class="empty-ico">—</div>Aucune donnée disponible</div>`;
      return;
    }
    const top = d.villes[0].nb_requetes || 1;
    document.getElementById('bodyPop').innerHTML = d.villes.map(v => {
      const rc  = v.rang <= 3 ? `r${v.rang}` : 'rn';
      const pct = Math.max(4, Math.round(v.nb_requetes / top * 100));
      return `<div class="pop-row" data-fn="goCity" data-a1="${esc(v.ville)}" data-a2="${esc(v.pays)}">
        <div class="rnk ${rc}">${v.rang}</div>
        <div><div class="pop-city">${fl(v.pays)} ${esc(v.ville)}</div><div class="pop-cc">${esc(v.pays)}</div></div>
        <div class="pop-bar"><div class="pop-fill" style="width:${pct}%"></div></div>
        <div class="pop-nb">${v.nb_requetes.toLocaleString('fr-FR')}</div>
      </div>`;
    }).join('');
  } catch(e) {
    document.getElementById('bodyPop').innerHTML = `<div class="empty"><div class="empty-ico">—</div>${esc(e.message)}</div>`;
  }
}

function goCity(v, p) { closeAll(); fetchAndOpen(v, p); }

function toggleSys() {
  document.getElementById('sysMenu').classList.toggle('open');
}

document.addEventListener('click', e => {
  if (!e.target.closest('.sys-nav')) document.getElementById('sysMenu').classList.remove('open');
  if (!e.target.closest('.s-wrap')) closeAC();
  const el = e.target.closest('[data-fn]');
  if (!el) return;
  switch (el.dataset.fn) {
    case 'clearSearch':  clearSearch(); break;
    case 'geoMe':        geoMe(); break;
    case 'toggleTheme':  toggleTheme(); break;
    case 'toggleSys':    toggleSys(); break;
    case 'closePanel':   closePanel(); break;
    case 'ptSwitch':     ptSwitch(el.dataset.a1); break;
    case 'openModal':    openModal(el.dataset.a1); break;
    case 'closeModal':   closeModal(el.dataset.a1); break;
    case 'closeAll':     closeAll(); break;
    case 'doExport':     doExport(el.dataset.a1); break;
    case 'goCity':       goCity(el.dataset.a1, el.dataset.a2); break;
    case 'pickAC':       pickAC(el); break;
    case 'toggleLive':   toggleLive(); break;
    case 'pipePick': {
      const ps = document.getElementById('pipeSection');
      if (ps) ps.classList.toggle('open');
      break;
    }
  }
});

function connectWS() {
  if (ws && ws.readyState === WebSocket.OPEN) return;
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws/stats`);
  ws.onmessage = evt => {
    try {
      const d = JSON.parse(evt.data);
      wsSysData = d;
      if (d.circuit_breakers) {
        const vals = Object.values(d.circuit_breakers);
        const nOk  = vals.filter(x => x === 'CLOSED').length;
        const dot  = document.getElementById('liveDot');
        if (dot) dot.style.background = nOk === vals.length ? 'var(--ok)' : nOk > 0 ? 'var(--warn)' : 'var(--err)';
        document.getElementById('srcLabel').textContent = `${nOk} source${nOk !== 1 ? 's' : ''}`;
        updateCbPastilles();
      }
      if (typeof d.nb_alertes === 'number') {
        const b = document.getElementById('bdgAlert');
        d.nb_alertes > 0
          ? (b.textContent = d.nb_alertes, b.classList.add('show'))
          : b.classList.remove('show');
      }
    } catch {}
  };
  ws.onclose = () => setTimeout(connectWS, 5000);
}

function toggleLive() {
  if (!cur) { showToast('Sélectionnez d\'abord une ville', 'warn'); return; }
  if (wsLive && wsLive.readyState <= 1) {
    wsLive.close();
    return;
  }
  startLiveWS(cur.name, cur.code);
}

function startLiveWS(ville, pays) {
  if (wsLive) wsLive.close();
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  wsLive = new WebSocket(`${proto}//${location.host}/ws/meteo/${encodeURIComponent(ville)}?pays=${encodeURIComponent(pays)}&interval=15`);
  document.getElementById('liveBtn').textContent = 'Arrêter';
  document.getElementById('liveBtn').classList.add('live-on');
  wsLive.onopen = () => {
    document.getElementById('phLive').classList.add('show');
    showToast('Flux temps réel activé', 'info');
  };
  wsLive.onmessage = evt => {
    try {
      const d = JSON.parse(evt.data);
      if (!cur) return;
      cur.data = d;
      const col = tColor(d.temperature_c);
      document.getElementById('pTemp').innerHTML   = `<span style="color:${col}">${d.temperature_c.toFixed(1)}</span><sup>°C</sup>`;
      document.getElementById('pCond').textContent = d.description;
      document.getElementById('pCond').style.color = col;
      document.getElementById('pIco').innerHTML    = wSvg(d.description, 58);
      updateCacheBadge(d);
      if (tab === 'donnees') showData(d);
    } catch {}
  };
  wsLive.onclose = () => {
    wsLive = null;
    document.getElementById('liveBtn').classList.remove('live-on');
    document.getElementById('liveBtn').textContent = 'Direct';
    document.getElementById('phLive').classList.remove('show');
  };
  wsLive.onerror = () => { if (wsLive) wsLive.close(); };
}

let _toastTimer = null;
function showToast(msg, type) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className   = 'show ' + (type || 'info');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(hideToast, 3500);
}
function hideToast() {
  document.getElementById('toast').classList.remove('show');
}

initMap();
loadAlertes();
connectWS();
sInp.focus();
