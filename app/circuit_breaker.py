import time
import threading
from enum import Enum
from typing import Callable, Any

from app.config import (
    CIRCUIT_BREAKER_THRESHOLD,
    CIRCUIT_BREAKER_RECOVERY,
    CIRCUIT_BREAKER_WINDOW,
)


class Etat(str, Enum):
    """Hérite de str pour être sérialisable en JSON sans conversion."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """
    Circuit breaker pour un fournisseur HTTP externe.

    CLOSED → OPEN après THRESHOLD erreurs dans WINDOW secondes.
    OPEN → HALF_OPEN après RECOVERY secondes.
    HALF_OPEN → CLOSED sur succès, OPEN sur échec.
    """

    def __init__(
        self,
        fournisseur: str,
        seuil: int = CIRCUIT_BREAKER_THRESHOLD,
        delai_recuperation: int = CIRCUIT_BREAKER_RECOVERY,
        fenetre: int = CIRCUIT_BREAKER_WINDOW,
    ):
        self.fournisseur = fournisseur
        self._seuil = seuil
        self._delai_recuperation = delai_recuperation
        self._fenetre = fenetre

        self._etat: Etat = Etat.CLOSED
        self._horodatages_erreurs: list[float] = []
        self._ouvert_depuis: float | None = None
        self._dernier_succes: float | None = None
        # Lock nécessaire car plusieurs coroutines peuvent appeler le CB simultanément
        self._lock = threading.Lock()

    @property
    def etat(self) -> Etat:
        with self._lock:
            self._evaluer_etat()
            return self._etat

    @property
    def nb_erreurs_recentes(self) -> int:
        with self._lock:
            self._purger_erreurs_anciennes()
            return len(self._horodatages_erreurs)

    @property
    def dernier_succes(self) -> float | None:
        return self._dernier_succes

    def _purger_erreurs_anciennes(self) -> None:
        """Supprime les erreurs hors de la fenêtre glissante (60s par défaut)."""
        limite = time.monotonic() - self._fenetre
        self._horodatages_erreurs = [t for t in self._horodatages_erreurs if t > limite]

    def _evaluer_etat(self) -> None:
        if self._etat == Etat.OPEN:
            ecoule = time.monotonic() - (self._ouvert_depuis or time.monotonic())
            if ecoule >= self._delai_recuperation:
                self._etat = Etat.HALF_OPEN
                self._horodatages_erreurs = []

    def peut_appeler(self) -> bool:
        with self._lock:
            self._evaluer_etat()

            if self._etat == Etat.CLOSED:
                return True

            if self._etat == Etat.OPEN:
                return False

            if self._etat == Etat.HALF_OPEN:
                # Repasser en OPEN avant de retourner True : garantit qu'une seule
                # requête de test passe, même si deux coroutines arrivent en même temps.
                self._etat = Etat.OPEN
                self._ouvert_depuis = time.monotonic()
                return True

        return False

    def enregistrer_succes(self) -> None:
        with self._lock:
            self._etat = Etat.CLOSED
            self._horodatages_erreurs = []
            self._ouvert_depuis = None
            self._dernier_succes = time.monotonic()

    def enregistrer_erreur(self) -> None:
        with self._lock:
            self._horodatages_erreurs.append(time.monotonic())
            self._purger_erreurs_anciennes()
            if len(self._horodatages_erreurs) >= self._seuil:
                self._etat = Etat.OPEN
                self._ouvert_depuis = time.monotonic()

    async def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """Exécute func en respectant l'état du circuit. Lève RuntimeError si OPEN."""
        if not self.peut_appeler():
            raise RuntimeError(
                f"Circuit OPEN pour '{self.fournisseur}' — fournisseur temporairement indisponible"
            )
        try:
            resultat = await func(*args, **kwargs)
            self.enregistrer_succes()
            return resultat
        except Exception as erreur:
            self.enregistrer_erreur()
            raise erreur


# Un circuit breaker par fournisseur, état en mémoire pour toute la durée du process.
# En multi-workers, les états ne seraient pas partagés (voir Redis pour ce cas).
circuit_breakers: dict[str, CircuitBreaker] = {
    "openweather": CircuitBreaker(fournisseur="openweather"),
    "open_meteo": CircuitBreaker(fournisseur="open_meteo"),
    "weatherapi": CircuitBreaker(fournisseur="weatherapi"),
}
