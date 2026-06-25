# ============================================================
# app/schemas.py — Schémas Pydantic de l'application météo
# ============================================================
# Ce fichier définit TOUS les modèles de données du projet.
# Pydantic valide automatiquement chaque valeur reçue et
# renvoie une erreur 422 claire si une contrainte est violée.
#
# Concepts utilisés (ch.06 du cours) :
#   - Field() avec contraintes (ge, le, min_length...)
#   - @field_validator pour la transformation de données
#   - @model_validator pour des règles qui croisent plusieurs champs
#   - Modèles imbriqués (objet dans un objet)
#   - Literal[] pour les énumérations simples
# ============================================================

from datetime import datetime, timezone  # Pour horodater les réponses
from typing import Literal              # Pour les types à valeurs fixes (énumérations)

from pydantic import (
    BaseModel,        # Classe de base de tous les schémas Pydantic
    Field,            # Permet d'ajouter des contraintes et métadonnées
    field_validator,  # Décorateur pour valider/transformer un champ (ch.06)
    model_validator,  # Décorateur pour valider plusieurs champs ensemble (ch.06)
)


# ============================================================
# SCHÉMAS D'ENTRÉE (ce que le client envoie)
# ============================================================

class MeteoRequest(BaseModel):
    """
    Paramètres de la requête GET /meteo.
    Ces champs arrivent en query string : /meteo?ville=Paris&pays=FR
    FastAPI les injecte automatiquement depuis l'URL.
    """

    # Nom de la ville : obligatoire, entre 1 et 100 caractères
    ville: str = Field(
        ...,                              # ... = champ obligatoire (pas de valeur par défaut)
        min_length=1,                     # Une ville vide n'a pas de sens
        max_length=100,                   # Limite raisonnable pour éviter les abus
        description="Nom de la ville (ex: Paris, Lomé, Montreal)",
        examples=["Paris", "Lomé", "New York"],
    )

    # Code pays ISO 3166-1 alpha-2 : optionnel, 2 lettres majuscules
    # Exemple : "FR" pour France, "TG" pour Togo, "US" pour États-Unis
    pays: str = Field(
        default="FR",                     # Valeur par défaut si non fourni
        min_length=2,
        max_length=2,                     # Exactement 2 caractères
        description="Code pays ISO 3166-1 alpha-2 (ex: FR, TG, US)",
        examples=["FR", "TG", "US"],
    )

    # ---- Validation du code pays : forcer les majuscules ----
    @field_validator("pays")
    @classmethod
    def pays_en_majuscules(cls, v: str) -> str:
        """
        Transforme le code pays en majuscules avant validation.
        Ex : "fr" -> "FR", "tg" -> "TG"
        Sans ce validator, "fr" serait refusé car il ne ressemble pas à "FR".
        Le validator s'exécute AVANT les contraintes de longueur.
        """
        return v.upper()  # "fr" -> "FR", "us" -> "US"

    # ---- Validation de la ville : supprimer les espaces superflus ----
    @field_validator("ville")
    @classmethod
    def ville_nettoyee(cls, v: str) -> str:
        """
        Supprime les espaces en début/fin de chaîne.
        Ex : "  Paris  " -> "Paris"
        """
        return v.strip()  # strip() supprime les espaces au début et à la fin


# ============================================================
# SCHÉMAS INTERNES (échange entre modules)
# ============================================================

class DonneesMeteo(BaseModel):
    """
    Données météo brutes retournées par UN fournisseur.
    Toutes les unités sont normalisées pour faciliter la fusion.
    Ce schéma est utilisé en interne entre les providers et la logique de fusion.
    """

    # Température en degrés Celsius (le fournisseur peut retourner des Kelvin
    # ou des Fahrenheit mais le provider normalise avant de remplir ce schéma)
    temperature_c: float = Field(
        ...,
        ge=-90.0,          # Record mondial de froid : -89.2°C (Antarctique)
        le=60.0,           # Record mondial de chaleur : +56.7°C (Californie)
        description="Température en degrés Celsius",
    )

    # Humidité relative en pourcentage (0% = air totalement sec, 100% = saturé)
    humidite_pct: float = Field(
        ...,
        ge=0.0,            # 0% = impossible dans la nature mais valide
        le=100.0,          # 100% = brouillard ou pluie
        description="Humidité relative en pourcentage",
    )

    # Vitesse du vent en km/h
    vent_kmh: float = Field(
        ...,
        ge=0.0,            # Le vent ne peut pas être négatif
        le=500.0,          # Vitesse max connue : ~408 km/h (cyclone record)
        description="Vitesse du vent en km/h",
    )

    # Description textuelle du temps (ex: "Ensoleillé", "Nuageux avec averses")
    description: str = Field(
        ...,
        min_length=1,
        max_length=200,    # Limite raisonnable
        description="Description textuelle du temps (ex: Ensoleillé)",
    )

    # Code météo normalisé entre fournisseurs.
    # On utilise les codes OpenWeather comme référence commune :
    # 800 = ciel dégagé, 500 = pluie légère, 200 = orage, 600 = neige légère
    code_meteo: int = Field(
        default=0,                        # 0 = inconnu/non fourni
        ge=0,
        description="Code météo normalisé (référence OpenWeather)",
    )


class ResultatFournisseur(BaseModel):
    """
    Résultat brut d'un fournisseur météo, avec métadonnées.
    Encapsule DonneesMeteo et ajoute des informations sur la source.
    Utilisé pour le cache court (5 min, niveau fournisseur).
    """

    # Identifiant du fournisseur (parmi les 3 supportés)
    # Literal[] = liste de valeurs autorisées → erreur si autre valeur
    fournisseur: Literal["openweather", "open_meteo", "weatherapi"] = Field(
        ...,
        description="Identifiant du fournisseur météo",
    )

    # Les données météo retournées par ce fournisseur (modèle imbriqué)
    # Pydantic valide récursivement le sous-objet
    donnees: DonneesMeteo = Field(
        ...,
        description="Données météo normalisées de ce fournisseur",
    )

    # Timestamp de récupération (pour savoir si les données sont fraîches)
    recupere_a: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),  # Rempli automatiquement à la création
        description="Date/heure de récupération (UTC)",
    )

    # Indique si ces données viennent du cache Redis ou d'un appel frais
    depuis_cache: bool = Field(
        default=False,
        description="True si ces données viennent du cache Redis",
    )


# ============================================================
# SCHÉMAS DE SORTIE (ce que l'API retourne au client)
# ============================================================

class MeteoResponse(BaseModel):
    """
    Réponse consolidée de GET /meteo.
    Fusionne les données des 3 fournisseurs et enrichit avec des métadonnées.
    C'est le schéma du cache long (1 heure, niveau consolidé).
    """

    # ---- Informations sur la ville demandée ----
    ville: str = Field(..., description="Nom de la ville demandée")
    pays: str = Field(..., description="Code pays (ex: FR)")

    # ---- Données météo fusionnées (moyennes des fournisseurs disponibles) ----
    temperature_c: float = Field(
        ...,
        description="Température moyenne des fournisseurs disponibles (°C)",
    )
    humidite_pct: float = Field(
        ...,
        description="Humidité relative moyenne (%)",
    )
    vent_kmh: float = Field(
        ...,
        description="Vitesse du vent moyenne (km/h)",
    )

    # Description choisie par vote majoritaire parmi les fournisseurs
    description: str = Field(
        ...,
        description="Description choisie par vote majoritaire",
    )

    # ---- Métadonnées sur les fournisseurs ----
    # Liste des fournisseurs qui ont répondu avec succès
    fournisseurs_ok: list[str] = Field(
        default_factory=list,             # Liste vide par défaut
        description="Fournisseurs qui ont répondu avec succès",
    )

    # Liste des fournisseurs qui ont échoué (en erreur ou circuit ouvert)
    fournisseurs_ko: list[str] = Field(
        default_factory=list,
        description="Fournisseurs en erreur ou circuit ouvert",
    )

    # Nombre de fournisseurs utilisés pour calculer les moyennes
    nb_sources: int = Field(
        ...,
        ge=1,                             # Au moins 1 source doit avoir répondu
        description="Nombre de fournisseurs utilisés pour la fusion",
    )

    # ---- Informations sur le cache ----
    depuis_cache: bool = Field(
        default=False,
        description="True si la réponse vient du cache Redis (niveau consolidé)",
    )

    # Horodatage de génération de la réponse
    genere_a: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Date/heure de génération de la réponse (UTC)",
    )

    # Indice de convergence inter-sources (0 = sources divergentes, 100 = accord parfait)
    # Calculé à partir de l'écart-type des températures entre fournisseurs.
    # Avec 1 seule source disponible, vaut 50 (confiance neutre — pas de comparaison possible).
    indice_confiance: int = Field(
        default=100,
        ge=0,
        le=100,
        description="Indice de confiance 0-100 basé sur la convergence des fournisseurs",
    )

    avertissement: str | None = Field(
        default=None,
        description="Message d'alerte si les données sont potentiellement incohérentes",
    )

    # ---- Validation transverse (model_validator, ch.06) ----
    @model_validator(mode="after")
    def verifier_coherence_sources(self):
        """
        Vérifie que le nombre de sources déclarées est cohérent
        avec les listes fournisseurs_ok et fournisseurs_ko.
        Si nb_sources != len(fournisseurs_ok), c'est un bug interne.
        mode='after' : tous les champs sont déjà validés individuellement.
        """
        if self.nb_sources != len(self.fournisseurs_ok):
            raise ValueError(
                f"Incohérence : nb_sources={self.nb_sources} mais "
                f"len(fournisseurs_ok)={len(self.fournisseurs_ok)}"
            )
        return self  # Obligatoire en mode 'after' : retourner l'instance


# ============================================================
# SCHÉMAS DE L'ENDPOINT /comparer
# ============================================================

class DonneesComparaison(BaseModel):
    """Données brutes d'un fournisseur pour la comparaison inter-sources."""
    temperature_c: float
    humidite_pct: float
    vent_kmh: float
    description: str
    depuis_cache: bool


class ComparaisonResponse(BaseModel):
    """
    Réponse de GET /comparer.
    Expose les données brutes de chaque fournisseur côte à côte
    avec les écarts de mesure et un indice de consensus global.
    """
    ville: str
    pays: str
    sources: dict[str, DonneesComparaison] = Field(
        default_factory=dict,
        description="Données brutes par fournisseur",
    )
    ecarts: dict[str, float] = Field(
        default_factory=dict,
        description="Écart max entre fournisseurs par métrique (ex: temperature_c → 1.5)",
    )
    indice_consensus: int = Field(
        ..., ge=0, le=100,
        description="Accord entre fournisseurs : 100 = accord parfait, 0 = forte divergence",
    )
    fournisseurs_ok: list[str] = Field(default_factory=list)
    fournisseurs_ko: list[str] = Field(default_factory=list)
    genere_a: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


# ============================================================
# SCHÉMAS DE L'ENDPOINT /sante (étape 8)
# ============================================================

class EtatCircuitBreaker(BaseModel):
    """
    État d'un circuit breaker pour un fournisseur donné.
    Retourné par GET /sante pour chaque fournisseur.
    """

    # Nom du fournisseur
    fournisseur: str = Field(..., description="Nom du fournisseur météo")

    # État du circuit breaker
    # CLOSED = normal (requêtes passent)
    # OPEN   = en erreur (requêtes bloquées)
    # HALF_OPEN = test de rétablissement (1 requête passe pour tester)
    etat: Literal["CLOSED", "OPEN", "HALF_OPEN"] = Field(
        ...,
        description="État du circuit breaker",
    )

    # Nombre d'erreurs consécutives depuis le dernier succès
    nb_erreurs: int = Field(default=0, ge=0, description="Nombre d'erreurs récentes")

    # Timestamp du dernier appel réussi (None si jamais appelé)
    dernier_succes: datetime | None = Field(
        default=None,
        description="Horodatage du dernier appel réussi (None si jamais appelé)",
    )


class SanteResponse(BaseModel):
    """
    Réponse complète de GET /sante.
    Donne l'état global de l'application et de chaque fournisseur.
    """

    # État global de l'application
    status: Literal["ok", "dégradé", "critique"] = Field(
        ...,
        description="ok = tout va bien, dégradé = 1-2 sources KO, critique = toutes KO",
    )

    # État détaillé de chaque fournisseur
    fournisseurs: list[EtatCircuitBreaker] = Field(
        ...,
        description="Liste détaillée de l'état de chaque fournisseur",
    )

    # Statistiques globales du cache Redis
    cache_hits: int = Field(
        default=0,
        ge=0,
        description="Nombre total de requêtes servies depuis le cache",
    )
    cache_misses: int = Field(
        default=0,
        ge=0,
        description="Nombre total de requêtes nécessitant un appel aux fournisseurs",
    )

    # Horodatage de la réponse
    verifie_a: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Date/heure de la vérification (UTC)",
    )