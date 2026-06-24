# ============================================================
# tests/conftest.py — Fixtures partagées par tous les tests
# ============================================================
# conftest.py est un fichier spécial reconnu automatiquement par pytest.
# Les fixtures définies ici sont disponibles dans TOUS les fichiers test_*.py
# sans avoir besoin de les importer.
#
# Concept (ch.08 exemple_03_fixtures_db) :
#   - fixture = fonction qui prépare un contexte de test
#   - Le paramètre scope contrôle la durée de vie de la fixture
#   - dependency_overrides permet de remplacer une dépendance FastAPI
#     par une version de test (ici : désactiver Redis pour les tests)
# ============================================================

import pytest
from fastapi.testclient import TestClient   # Client de test FastAPI (ch.08)

from app.main import app                    # L'application FastAPI à tester
from app import cache                       # Pour neutraliser Redis en tests
from app.circuit_breaker import circuit_breakers   # Pour réinitialiser les CB


# ============================================================
# FIXTURE : client de test FastAPI
# ============================================================
@pytest.fixture
def client():
    """
    Retourne un TestClient FastAPI configuré pour les tests.

    TestClient simule des requêtes HTTP sans serveur réseau réel.
    Il exécute les routes FastAPI directement en mémoire → ultra-rapide.

    scope par défaut = "function" : une nouvelle instance par test.
    Cela garantit l'isolation entre les tests.
    """
    # On désactive le scheduler pour les tests (pas besoin de threads en test)
    # On le fait en patchant la fonction demarrer_scheduler
    import app.main as main_module

    # Sauvegarde de la vraie fonction
    _vrai_demarrer = main_module.demarrer_scheduler
    _vrai_arreter  = main_module.arreter_scheduler

    # Remplacement par des fonctions vides (no-op) pendant les tests
    main_module.demarrer_scheduler = lambda: None
    main_module.arreter_scheduler  = lambda: None

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c   # Le test s'exécute ici

    # Restauration des vraies fonctions après le test
    main_module.demarrer_scheduler = _vrai_demarrer
    main_module.arreter_scheduler  = _vrai_arreter


# ============================================================
# FIXTURE : réinitialisation des circuit breakers
# ============================================================
@pytest.fixture(autouse=True)
def reset_circuit_breakers():
    """
    Réinitialise tous les circuit breakers à l'état CLOSED avant chaque test.

    autouse=True : appliquée automatiquement à TOUS les tests,
    sans avoir à la déclarer explicitement.

    Pourquoi ? Si un test simule des erreurs et ouvre un circuit,
    le test suivant hériterait de cet état → résultats imprévisibles.
    """
    from app.circuit_breaker import Etat

    for cb in circuit_breakers.values():
        # Réinitialisation manuelle de l'état interne
        cb._etat = Etat.CLOSED           # Retour à l'état normal
        cb._horodatages_erreurs = []     # Effacement des erreurs
        cb._ouvert_depuis = None         # Pas d'ouverture récente
        cb._dernier_succes = None        # Pas de succès connu

    yield   # Le test s'exécute ici

    # Pas de nettoyage post-test nécessaire (le prochain test aura sa propre fixture)


# ============================================================
# FIXTURE : neutralisation de Redis
# ============================================================
@pytest.fixture(autouse=True)
def sans_redis(monkeypatch):
    """
    Remplace toutes les fonctions du cache par des no-ops pendant les tests.

    Pourquoi ?
    1. Les tests ne doivent pas dépendre d'un serveur Redis externe
    2. Un cache fonctionnel masquerait les appels réels aux providers
       (un test pourrait passer grâce au cache, pas grâce au code)
    3. Les tests doivent être déterministes et reproductibles

    monkeypatch est une fixture pytest built-in qui permet de
    remplacer temporairement des fonctions/attributs.
    Elle restaure automatiquement les originaux après chaque test.
    """
    # Toutes les lectures de cache retournent None (cache miss)
    monkeypatch.setattr(cache, "lire_cache_long",  lambda *a, **k: None)
    monkeypatch.setattr(cache, "lire_cache_court", lambda *a, **k: None)

    # Toutes les écritures de cache sont ignorées
    monkeypatch.setattr(cache, "ecrire_cache_long",  lambda *a, **k: None)
    monkeypatch.setattr(cache, "ecrire_cache_court", lambda *a, **k: None)

    # Les compteurs et métriques sont ignorés
    monkeypatch.setattr(cache, "incrementer_compteur", lambda *a, **k: None)
    monkeypatch.setattr(cache, "enregistrer_hit",      lambda *a, **k: None)
    monkeypatch.setattr(cache, "enregistrer_miss",     lambda *a, **k: None)
    monkeypatch.setattr(cache, "obtenir_stats",
                        lambda *a, **k: {"hits": 0, "misses": 0, "ratio_pct": 0})

    yield   # Le test s'exécute ici avec Redis neutralisé
