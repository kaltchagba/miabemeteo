import re

import httpx
import pytest
import respx

import app.cache as cache_module
import app.scheduler as scheduler_module
from app.scheduler import (
    _prechauffer_ville,
    _tache_prechauffage,
    arreter_scheduler,
    demarrer_scheduler,
    VILLES_DEFAUT,
)
from app.schemas import MeteoResponse


_OW_OK = {
    "main": {"temp": 22.5, "humidity": 60},
    "wind": {"speed": 4.0},
    "weather": [{"description": "ciel dégagé", "id": 800}],
}

_OM_GEO_OK = {
    "results": [
        {"name": "Paris", "latitude": 48.8534, "longitude": 2.3488, "country": "France"}
    ]
}

_OM_FORECAST_OK = {
    "hourly": {
        "temperature_2m": [23.0, 22.0, 21.5],
        "relativehumidity_2m": [58, 57, 56],
        "windspeed_10m": [15.0, 14.0, 13.0],
        "weathercode": [0, 0, 1],
    }
}

_WA_OK = {
    "location": {},
    "current": {
        "temp_c": 21.8,
        "humidity": 62,
        "wind_kph": 13.0,
        "condition": {"text": "Ensoleillé", "code": 1000},
    },
}


@pytest.fixture
def cache_neutre(monkeypatch):
    """Neutralise toutes les fonctions Redis pour les tests du scheduler."""
    monkeypatch.setattr(cache_module, "lire_cache_long", lambda *a: None)
    monkeypatch.setattr(cache_module, "ecrire_cache_long", lambda *a: None)
    monkeypatch.setattr(cache_module, "lire_cache_court", lambda *a: None)
    monkeypatch.setattr(cache_module, "ecrire_cache_court", lambda *a: None)
    monkeypatch.setattr(cache_module, "incrementer_compteur", lambda *a: None)
    monkeypatch.setattr(cache_module, "enregistrer_hit", lambda: None)
    monkeypatch.setattr(cache_module, "enregistrer_miss", lambda: None)
    monkeypatch.setattr(
        cache_module, "obtenir_stats", lambda: {"hits": 0, "misses": 0, "ratio_pct": 0}
    )


@respx.mock
async def test_prechauffer_ville_cache_valide(monkeypatch):
    """Cache long encore frais → retour immédiat, aucun appel HTTP émis."""
    en_cache = MeteoResponse(
        ville="Paris",
        pays="FR",
        temperature_c=20.0,
        humidite_pct=55.0,
        vent_kmh=10.0,
        description="Ensoleillé",
        fournisseurs_ok=["openweather"],
        fournisseurs_ko=[],
        nb_sources=1,
    )
    monkeypatch.setattr(cache_module, "lire_cache_long", lambda v, p: en_cache)

    async with httpx.AsyncClient() as client:
        await _prechauffer_ville(client, "Paris", "FR")

    assert len(respx.calls) == 0


@respx.mock
async def test_prechauffer_ville_succes(cache_neutre, monkeypatch):
    """3 providers répondent → réponse consolidée écrite dans le cache long."""
    respx.get(re.compile(r"https://api\.openweathermap\.org/.*")).mock(
        return_value=httpx.Response(200, json=_OW_OK)
    )
    respx.get(re.compile(r"https://geocoding-api\.open-meteo\.com/.*")).mock(
        return_value=httpx.Response(200, json=_OM_GEO_OK)
    )
    respx.get(re.compile(r"https://api\.open-meteo\.com/.*")).mock(
        return_value=httpx.Response(200, json=_OM_FORECAST_OK)
    )
    respx.get(re.compile(r"https://api\.weatherapi\.com/.*")).mock(
        return_value=httpx.Response(200, json=_WA_OK)
    )

    ecritures = []
    monkeypatch.setattr(
        cache_module,
        "ecrire_cache_long",
        lambda r, v, p: ecritures.append((v, p)),
    )

    async with httpx.AsyncClient() as client:
        await _prechauffer_ville(client, "Paris", "FR")

    assert len(ecritures) == 1
    assert ecritures[0] == ("Paris", "FR")


@respx.mock
async def test_prechauffer_ville_tous_providers_ko(cache_neutre, monkeypatch):
    """Tous les providers retournent 503 → pas d'exception, pas d'écriture."""
    respx.get(re.compile(r"https://api\.openweathermap\.org/.*")).mock(
        return_value=httpx.Response(503)
    )
    respx.get(re.compile(r"https://geocoding-api\.open-meteo\.com/.*")).mock(
        return_value=httpx.Response(503)
    )
    respx.get(re.compile(r"https://api\.open-meteo\.com/.*")).mock(
        return_value=httpx.Response(503)
    )
    respx.get(re.compile(r"https://api\.weatherapi\.com/.*")).mock(
        return_value=httpx.Response(503)
    )

    ecritures = []
    monkeypatch.setattr(
        cache_module, "ecrire_cache_long", lambda *a: ecritures.append(a)
    )

    async with httpx.AsyncClient() as client:
        await _prechauffer_ville(client, "Paris", "FR")

    assert len(ecritures) == 0


async def test_tache_prechauffage_utilise_villes_defaut(monkeypatch):
    """Sans données Redis, la tâche préchaffe les villes de la liste par défaut."""
    monkeypatch.setattr(cache_module, "obtenir_villes_populaires", lambda n: [])

    prechaufees = []

    async def _mock(client, ville, pays):
        prechaufees.append((ville, pays))

    monkeypatch.setattr(scheduler_module, "_prechauffer_ville", _mock)

    await _tache_prechauffage()

    assert ("Paris", "FR") in prechaufees
    assert ("Lomé", "TG") in prechaufees
    assert len(prechaufees) == len(VILLES_DEFAUT)


async def test_tache_prechauffage_utilise_villes_populaires(monkeypatch):
    """Avec des données Redis, la tâche utilise les villes les plus demandées."""
    monkeypatch.setattr(
        cache_module,
        "obtenir_villes_populaires",
        lambda n: [("berlin", "de"), ("tokyo", "jp")],
    )

    prechaufees = []

    async def _mock(client, ville, pays):
        prechaufees.append((ville, pays))

    monkeypatch.setattr(scheduler_module, "_prechauffer_ville", _mock)

    await _tache_prechauffage()

    assert ("Berlin", "DE") in prechaufees
    assert ("Tokyo", "JP") in prechaufees
    assert len(prechaufees) == 2


async def test_tache_prechauffage_resiliente_aux_erreurs(monkeypatch):
    """Une erreur sur une ville n'interrompt pas le traitement des suivantes."""
    monkeypatch.setattr(
        cache_module,
        "obtenir_villes_populaires",
        lambda n: [("ville_ko", "xx"), ("paris", "fr")],
    )

    prechaufees = []

    async def _mock(client, ville, pays):
        if ville == "Ville_Ko":
            raise RuntimeError("Échec simulé")
        prechaufees.append(ville)

    monkeypatch.setattr(scheduler_module, "_prechauffer_ville", _mock)

    await _tache_prechauffage()

    assert "Paris" in prechaufees


def test_demarrer_et_arreter_scheduler():
    """Le scheduler démarre (thread actif) puis s'arrête proprement."""
    scheduler = demarrer_scheduler()
    try:
        assert scheduler.running
    finally:
        arreter_scheduler()

    assert not scheduler.running
