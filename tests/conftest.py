import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.circuit_breaker import circuit_breakers
from app.router_meteo import ServiceCache, get_service_cache


class _CacheMock(ServiceCache):
    """Cache bouchon : lectures → None, écritures ignorées (force les appels réseau)."""

    def lire_cache_long(self, ville, pays):
        return None

    def ecrire_cache_long(self, reponse, ville, pays):
        pass

    def lire_cache_court(self, fournisseur, ville, pays):
        return None

    def ecrire_cache_court(self, resultat, ville, pays):
        pass

    def incrementer_compteur(self, ville, pays):
        pass

    def enregistrer_hit(self):
        pass

    def enregistrer_miss(self):
        pass

    def obtenir_stats(self):
        return {"hits": 0, "misses": 0, "ratio_pct": 0}


@pytest.fixture
def client():
    """TestClient avec scheduler neutralisé et cache remplacé par _CacheMock."""
    import app.main as main_module

    _vrai_demarrer = main_module.demarrer_scheduler
    _vrai_arreter  = main_module.arreter_scheduler

    main_module.demarrer_scheduler = lambda: None
    main_module.arreter_scheduler  = lambda: None

    app.dependency_overrides[get_service_cache] = lambda: _CacheMock()

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()
    main_module.demarrer_scheduler = _vrai_demarrer
    main_module.arreter_scheduler  = _vrai_arreter


@pytest.fixture(autouse=True)
def reset_circuit_breakers():
    """Remet tous les CB à CLOSED avant chaque test pour éviter les contaminations."""
    from app.circuit_breaker import Etat

    for cb in circuit_breakers.values():
        cb._etat = Etat.CLOSED
        cb._horodatages_erreurs = []
        cb._ouvert_depuis = None
        cb._dernier_succes = None

    yield
