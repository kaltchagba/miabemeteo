import asyncio
import csv
import io
import json
import logging
import re
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from app import cache as cache_module
from app.providers import open_meteo
from app.router_meteo import (
    ServiceCache,
    get_http_client,
    get_service_cache,
    _fetch_meteo,
)
from app.schemas import (
    AlerteMeteo,
    AlertesResponse,
    BatchRequest,
    BatchResponse,
    HistoriqueResponse,
    EntreeHistorique,
    JourPrevision,
    PrevisionsResponse,
    ResultatBatch,
    VillePopulaire,
    VillesPopulairesResponse,
    MeteoResponse,
)
from app.circuit_breaker import circuit_breakers

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Météo"])


@router.get(
    "/historique",
    response_model=HistoriqueResponse,
    summary="Historique de températures d'une ville",
    description=(
        "Retourne les N dernières mesures enregistrées (fenêtre glissante de 48 relevés). "
        "Calcule la tendance et les extrêmes."
    ),
    responses={
        200: {"description": "Historique de températures"},
        404: {"description": "Aucun historique disponible"},
    },
)
async def get_historique(
    ville: str = Query(..., min_length=1, max_length=100, pattern=r"^[\w\s\-\'\.\,À-ɏ]+$", examples=["Paris"]),
    pays: str = Query(default="FR", min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$", examples=["FR"]),
    n: int = Query(default=24, ge=1, le=48, description="Nombre d'entrées à retourner"),
) -> HistoriqueResponse:
    ville = ville.strip()
    pays  = pays.strip().upper()

    entrees_raw = cache_module.lire_historique(ville, pays, n)
    if not entrees_raw:
        raise HTTPException(
            status_code=404,
            detail={
                "erreur": f"Aucun historique pour {ville}, {pays}",
                "suggestion": "Effectuez d'abord une requête GET /meteo pour cette ville.",
            },
        )

    entrees      = [EntreeHistorique(**e) for e in entrees_raw]
    temperatures = [e.temperature_c for e in entrees]

    tendance = "stable"
    if len(temperatures) >= 4:
        debut_moy = sum(temperatures[:2]) / 2
        fin_moy   = sum(temperatures[-2:]) / 2
        delta = fin_moy - debut_moy
        if delta > 1.5:
            tendance = "hausse"
        elif delta < -1.5:
            tendance = "baisse"

    return HistoriqueResponse(
        ville=ville, pays=pays,
        entrees=entrees, tendance=tendance,
        temp_min=round(min(temperatures), 1),
        temp_max=round(max(temperatures), 1),
        nb_entrees=len(entrees),
    )


@router.get(
    "/villes-populaires",
    response_model=VillesPopulairesResponse,
    summary="Classement des villes les plus consultées",
    description="Retourne le top N des villes par nombre de requêtes (Redis Sorted Set).",
)
async def get_villes_populaires(
    n: int = Query(default=10, ge=1, le=50),
) -> VillesPopulairesResponse:
    villes_raw = cache_module.obtenir_top_villes_score(n)
    total      = cache_module.obtenir_total_requetes()
    return VillesPopulairesResponse(
        villes=[VillePopulaire(**v) for v in villes_raw],
        total_requetes=total,
    )


@router.get(
    "/alertes",
    response_model=AlertesResponse,
    summary="Alertes météo actives",
    description=(
        "Retourne les alertes actives : canicule (>35°C), gel (<0°C), vent fort (>80 km/h). "
        "Émises automatiquement lors des appels /meteo, expirent après 1h."
    ),
)
async def get_alertes() -> AlertesResponse:
    alertes_raw = cache_module.lire_alertes_actives()
    return AlertesResponse(alertes=[AlerteMeteo(**a) for a in alertes_raw], nb_actives=len(alertes_raw))


@router.get(
    "/previsions",
    response_model=PrevisionsResponse,
    summary="Prévisions météo sur 7 jours",
    description=(
        "Interroge Open-Meteo pour les prévisions journalières (min/max/précipitations). "
        "Open-Meteo est l'unique provider utilisé ici car il expose nativement un forecast daily."
    ),
    responses={
        200: {"description": "Prévisions sur 7 jours"},
        503: {"description": "Open-Meteo indisponible"},
    },
)
async def get_previsions(
    ville: str = Query(..., min_length=1, max_length=100, pattern=r"^[\w\s\-\'\.\,À-ɏ]+$", examples=["Paris"]),
    pays: str = Query(default="FR", min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$", examples=["FR"]),
    jours: int = Query(default=7, ge=1, le=16),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> PrevisionsResponse:
    ville = ville.strip()
    pays  = pays.strip().upper()
    try:
        donnees = await open_meteo.fetch_previsions(client, ville, pays, nb_jours=jours)
    except ValueError as e:
        raise HTTPException(status_code=404, detail={"erreur": str(e)})
    except Exception as e:
        logger.error("Erreur prévisions %s,%s : %s", ville, pays, e)
        raise HTTPException(status_code=503, detail={"erreur": "Open-Meteo indisponible", "detail": str(e)})

    return PrevisionsResponse(ville=ville, pays=pays, jours=[JourPrevision(**j) for j in donnees])


@router.get(
    "/export",
    summary="Export des données météo",
    description=(
        "Retourne les données météo d'une ville au format CSV ou JSON téléchargeable. "
        "Inclut l'historique si disponible."
    ),
)
async def export_meteo(
    ville: str = Query(..., min_length=1, max_length=100, pattern=r"^[\w\s\-\'\.\,À-ɏ]+$", examples=["Paris"]),
    pays: str = Query(default="FR", min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$", examples=["FR"]),
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    client: httpx.AsyncClient = Depends(get_http_client),
    cache_svc: ServiceCache = Depends(get_service_cache),
) -> StreamingResponse:
    ville = ville.strip()
    pays  = pays.strip().upper()

    try:
        meteo = await _fetch_meteo(ville, pays, client, cache_svc)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

    historique = cache_module.lire_historique(ville, pays, 48)

    # Sanitisation du nom de fichier pour éviter toute injection d'en-tête HTTP
    _safe = re.sub(r"[^\w\-]", "_", f"{ville.lower()}_{pays.lower()}")
    maintenant  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    nom_fichier = f"meteo_{_safe}_{maintenant}"

    if format == "json":
        contenu = {
            "ville": ville, "pays": pays,
            "meteo_actuelle": meteo.model_dump(mode="json"),
            "historique": historique,
            "exporte_a": datetime.now(timezone.utc).isoformat(),
        }
        return StreamingResponse(
            io.BytesIO(json.dumps(contenu, ensure_ascii=False, indent=2).encode("utf-8")),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{nom_fichier}.json"'},
        )

    sortie = io.StringIO()
    writer = csv.writer(sortie, delimiter=";")
    writer.writerow([
        "timestamp", "ville", "pays",
        "temperature_c", "humidite_pct", "vent_kmh",
        "description", "nb_sources", "indice_confiance", "depuis_cache",
    ])
    writer.writerow([
        meteo.genere_a.isoformat(), meteo.ville, meteo.pays,
        meteo.temperature_c, meteo.humidite_pct, meteo.vent_kmh,
        meteo.description, meteo.nb_sources, meteo.indice_confiance, meteo.depuis_cache,
    ])
    for entree in historique:
        writer.writerow([
            entree.get("timestamp", ""), ville, pays,
            entree.get("temperature_c", ""), entree.get("humidite_pct", ""),
            entree.get("vent_kmh", ""), entree.get("description", ""),
            "", "", "historique",
        ])

    contenu_csv = "﻿" + sortie.getvalue()  # BOM UTF-8 pour Excel
    return StreamingResponse(
        io.BytesIO(contenu_csv.encode("utf-8")),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nom_fichier}.csv"'},
    )


@router.post(
    "/batch",
    response_model=BatchResponse,
    summary="Météo de plusieurs villes en une requête",
    description="Interroge jusqu'à 10 villes en parallèle via asyncio.gather().",
)
async def batch_meteo(
    body: BatchRequest,
    client: httpx.AsyncClient = Depends(get_http_client),
    cache_svc: ServiceCache = Depends(get_service_cache),
) -> BatchResponse:

    async def _une_ville(ville: str, pays: str) -> ResultatBatch:
        try:
            donnees = await _fetch_meteo(ville, pays, client, cache_svc)
            return ResultatBatch(ville=ville, pays=pays, succes=True, donnees=donnees)
        except HTTPException as e:
            detail = e.detail
            msg = detail.get("erreur", str(detail)) if isinstance(detail, dict) else str(detail)
            return ResultatBatch(ville=ville, pays=pays, succes=False, erreur=msg)
        except Exception as e:
            return ResultatBatch(ville=ville, pays=pays, succes=False, erreur=str(e))

    resultats: list[ResultatBatch] = await asyncio.gather(*[
        _une_ville(v.ville, v.pays) for v in body.villes
    ])
    return BatchResponse(
        resultats=resultats,
        nb_succes=sum(1 for r in resultats if r.succes),
        nb_erreurs=sum(1 for r in resultats if not r.succes),
    )


@router.websocket("/ws/stats")
async def ws_stats(websocket: WebSocket) -> None:
    """Pousse les métriques de l'application toutes les 5 secondes (cache, CB, top villes)."""
    await websocket.accept()
    logger.info("WebSocket /ws/stats ouvert")
    try:
        while True:
            stats    = cache_module.obtenir_stats()
            cb_etats = {pid: cb.etat.value for pid, cb in circuit_breakers.items()}
            top      = cache_module.obtenir_top_villes_score(5)
            alertes  = cache_module.lire_alertes_actives()

            await websocket.send_json({
                "cache":            stats,
                "circuit_breakers": cb_etats,
                "top_villes":       top,
                "nb_alertes":       len(alertes),
                "timestamp":        datetime.now(timezone.utc).isoformat(),
            })
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        logger.info("WebSocket /ws/stats fermé")
    except Exception as e:
        logger.error("Erreur WebSocket /ws/stats : %s", e)
