# ============================================================
# app/circuit_breaker.py — Circuit Breaker par fournisseur
# ============================================================
# Le circuit breaker est un pattern de résilience qui protège
# l'application quand un fournisseur externe devient défaillant.
#
# Fonctionnement : 3 états possibles
#
#   CLOSED   → état normal, toutes les requêtes passent
#              Si le nb d'erreurs dépasse le seuil → passe en OPEN
#
#   OPEN     → le fournisseur est considéré en panne
#              Les requêtes sont BLOQUÉES immédiatement (pas d'appel HTTP)
#              Après CIRCUIT_BREAKER_RECOVERY secondes → passe en HALF_OPEN
#
#   HALF_OPEN → état de test : on laisse passer UNE seule requête
#              Si elle réussit → retour en CLOSED (fournisseur rétabli)
#              Si elle échoue → retour en OPEN (on attend encore)
#
# Schéma de transition :
#
#   CLOSED ──(trop d'erreurs)──→ OPEN
#     ↑                            │
#     └──(succès test)── HALF_OPEN ←─(délai écoulé)
#
# ============================================================

import time                           # Pour mesurer le temps écoulé (time.monotonic)
import threading                      # Pour thread-safety (Lock)
from enum import Enum                 # Pour définir les 3 états comme une énumération
from typing import Callable, Any      # Types génériques pour la méthode call()

from app.config import (
    CIRCUIT_BREAKER_THRESHOLD,        # Nb d'erreurs avant ouverture (défaut: 3)
    CIRCUIT_BREAKER_RECOVERY,         # Secondes avant HALF_OPEN (défaut: 30)
    CIRCUIT_BREAKER_WINDOW,           # Fenêtre de temps pour compter les erreurs
)


# ============================================================
# ÉNUMÉRATION DES ÉTATS
# ============================================================
class Etat(str, Enum):
    """
    Les 3 états possibles du circuit breaker.
    Hérite de str pour être sérialisable en JSON sans transformation.
    Ex : Etat.CLOSED vaut la chaîne "CLOSED" directement.
    """
    CLOSED    = "CLOSED"      # Circuit fermé = normal = requêtes autorisées
    OPEN      = "OPEN"        # Circuit ouvert = panne = requêtes bloquées
    HALF_OPEN = "HALF_OPEN"   # Semi-ouvert = test = 1 requête autorisée


# ============================================================
# CLASSE PRINCIPALE : CircuitBreaker
# ============================================================
class CircuitBreaker:
    """
    Circuit breaker pour un fournisseur HTTP externe.

    Usage dans le code appelant :
        cb = CircuitBreaker(fournisseur="openweather")

        # Option 1 : vérification manuelle
        if cb.peut_appeler():
            try:
                data = await provider.fetch(...)
                cb.enregistrer_succes()
            except Exception as e:
                cb.enregistrer_erreur()

        # Option 2 : méthode call() qui gère tout automatiquement
        data = await cb.call(provider.fetch, client, ville, pays)
    """

    def __init__(
        self,
        fournisseur: str,
        seuil: int = CIRCUIT_BREAKER_THRESHOLD,
        delai_recuperation: int = CIRCUIT_BREAKER_RECOVERY,
        fenetre: int = CIRCUIT_BREAKER_WINDOW,
    ):
        """
        Initialise le circuit breaker.

        Paramètres :
            fournisseur        — nom du fournisseur (ex: "openweather")
            seuil              — nb d'erreurs avant ouverture du circuit
            delai_recuperation — secondes avant de tenter la récupération
            fenetre            — fenêtre de temps (s) pour compter les erreurs
        """
        # Identifiant du fournisseur associé à ce circuit breaker
        self.fournisseur = fournisseur

        # Configuration
        self._seuil = seuil                         # Ex : 3 erreurs → circuit OPEN
        self._delai_recuperation = delai_recuperation  # Ex : 30 secondes
        self._fenetre = fenetre                     # Ex : 60 secondes

        # ---- État interne ----
        self._etat: Etat = Etat.CLOSED              # Commence toujours en CLOSED

        # Horodatages des erreurs récentes (en secondes, time.monotonic)
        # On utilise une liste plutôt qu'un simple compteur pour pouvoir
        # éliminer les erreurs trop anciennes (hors de la fenêtre)
        self._horodatages_erreurs: list[float] = []

        # Moment où le circuit est passé en OPEN (pour calculer le délai)
        self._ouvert_depuis: float | None = None

        # Moment du dernier appel réussi (pour les stats dans /sante)
        self._dernier_succes: float | None = None

        # Lock threading pour éviter les conditions de course si plusieurs
        # coroutines accèdent au circuit breaker simultanément
        self._lock = threading.Lock()

    # ============================================================
    # PROPRIÉTÉS PUBLIQUES (lecture seule)
    # ============================================================

    @property
    def etat(self) -> Etat:
        """Retourne l'état actuel du circuit (peut déclencher une transition)."""
        # On appelle _evaluer_etat() pour mettre à jour si le délai est écoulé
        with self._lock:
            self._evaluer_etat()
            return self._etat

    @property
    def nb_erreurs_recentes(self) -> int:
        """Retourne le nombre d'erreurs dans la fenêtre de temps courante."""
        with self._lock:
            self._purger_erreurs_anciennes()
            return len(self._horodatages_erreurs)

    @property
    def dernier_succes(self) -> float | None:
        """Timestamp (monotonic) du dernier appel réussi. None si jamais appelé."""
        return self._dernier_succes

    # ============================================================
    # MÉTHODES INTERNES (prefixe _ = usage interne uniquement)
    # ============================================================

    def _purger_erreurs_anciennes(self) -> None:
        """
        Supprime les erreurs dont l'horodatage est en dehors de la fenêtre.
        Ex : si fenêtre = 60s, on supprime les erreurs de plus de 60 secondes.
        Doit être appelée AVANT de lire len(self._horodatages_erreurs).
        ATTENTION : doit être appelée depuis un bloc with self._lock.
        """
        maintenant = time.monotonic()                # Temps en secondes (non affecté par NTP)
        limite = maintenant - self._fenetre          # Timestamp de la limite

        # On garde seulement les erreurs plus récentes que la limite
        self._horodatages_erreurs = [
            t for t in self._horodatages_erreurs
            if t > limite                            # True = erreur dans la fenêtre
        ]

    def _evaluer_etat(self) -> None:
        """
        Réévalue l'état du circuit breaker selon les règles de transition.
        OPEN → HALF_OPEN si le délai de récupération est écoulé.
        Doit être appelée depuis un bloc with self._lock.
        """
        # Seul état qui peut changer automatiquement avec le temps : OPEN
        if self._etat == Etat.OPEN:
            maintenant = time.monotonic()
            temps_ecoule = maintenant - (self._ouvert_depuis or maintenant)

            # Si le délai de récupération est écoulé → passer en HALF_OPEN
            if temps_ecoule >= self._delai_recuperation:
                self._etat = Etat.HALF_OPEN
                # On vide les erreurs pour repartir proprement en HALF_OPEN
                self._horodatages_erreurs = []

    # ============================================================
    # MÉTHODES PUBLIQUES : API principale du circuit breaker
    # ============================================================

    def peut_appeler(self) -> bool:
        """
        Indique si une requête vers le fournisseur est autorisée.

        Retourne :
            True  → requête autorisée (état CLOSED ou HALF_OPEN)
            False → requête bloquée (état OPEN)

        En HALF_OPEN, retourne True une seule fois pour le test.
        Les requêtes suivantes sont bloquées jusqu'au résultat du test.
        """
        with self._lock:
            self._evaluer_etat()   # Met à jour OPEN → HALF_OPEN si délai écoulé

            if self._etat == Etat.CLOSED:
                return True        # Tout va bien → on laisse passer

            if self._etat == Etat.OPEN:
                return False       # En panne → on bloque

            if self._etat == Etat.HALF_OPEN:
                # En HALF_OPEN, on laisse passer UNE requête de test.
                # On passe temporairement en OPEN pour bloquer les suivantes
                # pendant que la requête de test est en cours.
                self._etat = Etat.OPEN
                self._ouvert_depuis = time.monotonic()
                return True        # Laisser passer cette requête de test

        return False               # Sécurité : bloquer si état inconnu

    def enregistrer_succes(self) -> None:
        """
        Enregistre un appel réussi.
        Remet le circuit en CLOSED et réinitialise les compteurs d'erreurs.
        """
        with self._lock:
            self._etat = Etat.CLOSED              # Retour à la normale
            self._horodatages_erreurs = []        # On efface toutes les erreurs
            self._ouvert_depuis = None            # Le circuit n'est plus ouvert
            self._dernier_succes = time.monotonic()  # Timestamp du succès

    def enregistrer_erreur(self) -> None:
        """
        Enregistre un appel échoué.
        Si le nombre d'erreurs récentes dépasse le seuil → ouvre le circuit.
        """
        with self._lock:
            # Ajoute l'horodatage de cette erreur à la liste
            self._horodatages_erreurs.append(time.monotonic())

            # Supprime les erreurs trop anciennes (hors fenêtre)
            self._purger_erreurs_anciennes()

            # Vérifie si le seuil est atteint
            if len(self._horodatages_erreurs) >= self._seuil:
                # Seuil dépassé → ouvrir le circuit
                self._etat = Etat.OPEN
                self._ouvert_depuis = time.monotonic()

    async def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """
        Exécute une fonction async en respectant l'état du circuit breaker.
        C'est la méthode la plus pratique : elle gère tout automatiquement.

        Usage :
            data = await cb.call(provider.fetch, client, ville, pays)

        Paramètres :
            func   — fonction async à exécuter (ex: openweather.fetch)
            *args  — arguments positionnels passés à func
            **kwargs — arguments nommés passés à func

        Retourne :
            Le résultat de func(*args, **kwargs)

        Lève :
            RuntimeError    — si le circuit est OPEN (requête bloquée)
            Exception       — toute erreur levée par func (après l'avoir enregistrée)
        """

        # Vérifie si l'appel est autorisé
        if not self.peut_appeler():
            # Le circuit est OPEN : on bloque sans faire d'appel HTTP
            raise RuntimeError(
                f"Circuit OPEN pour '{self.fournisseur}' — "
                f"fournisseur temporairement indisponible"
            )

        try:
            # Exécution de la fonction async (appel HTTP réel)
            resultat = await func(*args, **kwargs)

            # L'appel a réussi → réinitialiser le circuit
            self.enregistrer_succes()

            return resultat

        except Exception as erreur:
            # L'appel a échoué → enregistrer l'erreur
            # (peut ouvrir le circuit si seuil atteint)
            self.enregistrer_erreur()

            # On re-lève l'exception pour que l'appelant puisse la traiter
            # (ex: asyncio.gather avec return_exceptions=True la capturera)
            raise erreur


# ============================================================
# REGISTRE GLOBAL DES CIRCUIT BREAKERS
# ============================================================
# Un circuit breaker par fournisseur, partagé par toute l'application.
# On les instancie une seule fois au démarrage (singleton).
# L'état persiste entre les requêtes tant que l'app tourne.
#
# NOTE : en multi-workers (uvicorn --workers 2), chaque worker a
# son propre espace mémoire → les états ne sont pas partagés.
# Pour un vrai partage, il faudrait stocker l'état dans Redis.
# Pour ce projet, un worker suffit.
circuit_breakers: dict[str, CircuitBreaker] = {
    "openweather": CircuitBreaker(fournisseur="openweather"),
    "open_meteo":  CircuitBreaker(fournisseur="open_meteo"),
    "weatherapi":  CircuitBreaker(fournisseur="weatherapi"),
}