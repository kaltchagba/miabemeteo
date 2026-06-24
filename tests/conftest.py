# ============================================================
# tests/conftest.py — Fixtures partagées par tous les tests
# ============================================================

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.circuit_breaker import circuit_breakers
from app.router_meteo import ServiceCache, get_service_cache


# ============================================================
# BOUCHON DU CACHE
# ============================================================

class _CacheMock(ServiceCache):
    """
    Implémentation bouchon du cache pour les tests d'intégration.

    Toutes les lectures retournent None (cache miss systématique) et
    toutes les écritures sont ignorées, ce qui garantit que chaque test
    exerce le chemin réseau sans être influencé par un état de cache
    précédent.
    """

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


# ============================================================
# FIXTURE : client de test FastAPI
# ============================================================

@pytest.fixture
def client():
    """
    Retourne un TestClient FastAPI configuré pour les tests.

    Le scheduler est neutralisé (pas de threads en arrière-plan) et
    le cache est remplacé par _CacheMock via dependency_overrides,
    ce qui isole chaque test de Redis sans modifier le code applicatif.

    scope par défaut = "function" : une instance fraîche par test.
    """
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


# ============================================================
# FIXTURE : réinitialisation des circuit breakers
# ============================================================

@pytest.fixture(autouse=True)
def reset_circuit_breakers():
    """
    Remet tous les circuit breakers à CLOSED avant chaque test.

    autouse=True : appliquée automatiquement sans déclaration explicite.
    Sans cette fixture, un test qui ouvre un circuit contaminerait les
    tests suivants et produirait des résultats non déterministes.
    """
    from app.circuit_breaker import Etat

    for cb in circuit_breakers.values():
        cb._etat = Etat.CLOSED
        cb._horodatages_erreurs = []
        cb._ouvert_depuis = None
        cb._dernier_succes = None

    yield
