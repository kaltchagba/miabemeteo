from prometheus_client import Counter, Gauge, Histogram

# Appels HTTP vers chaque fournisseur météo
APPELS_FOURNISSEUR = Counter(
    "meteo_provider_appels_total",
    "Nombre total d'appels vers chaque fournisseur météo",
    ["fournisseur", "statut"],  # statut: succes | erreur | circuit_ouvert
)

# Latence des appels HTTP (secondes)
LATENCE_FOURNISSEUR = Histogram(
    "meteo_provider_latence_secondes",
    "Durée des appels HTTP aux fournisseurs météo",
    ["fournisseur"],
    buckets=[0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 2.0, 5.0],
)

# État des circuit breakers : 0=CLOSED, 1=HALF_OPEN, 2=OPEN
ETAT_CIRCUIT_BREAKER = Gauge(
    "meteo_circuit_breaker_etat",
    "État du circuit breaker (0=CLOSED, 1=HALF_OPEN, 2=OPEN)",
    ["fournisseur"],
)

# Opérations de cache par niveau et type
OPERATIONS_CACHE = Counter(
    "meteo_cache_operations_total",
    "Nombre d'opérations de cache par niveau et résultat",
    ["niveau", "operation"],  # niveau: l1|l2 — operation: hit|miss
)

# Mapping état → valeur numérique (pour ETAT_CIRCUIT_BREAKER)
_VALEUR_ETAT = {"CLOSED": 0, "HALF_OPEN": 1, "OPEN": 2}


def maj_circuit_breaker(fournisseur: str, etat: str) -> None:
    """Met à jour la jauge Prometheus pour l'état d'un circuit breaker."""
    ETAT_CIRCUIT_BREAKER.labels(fournisseur=fournisseur).set(_VALEUR_ETAT.get(etat, 0))
