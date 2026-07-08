from datetime import datetime, timezone
from typing import Literal

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
)


class MeteoRequest(BaseModel):
    """Paramètres de la requête GET /meteo (query string : ville + pays)."""

    ville: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Nom de la ville (ex: Paris, Lomé, Montreal)",
        examples=["Paris", "Lomé", "New York"],
    )
    pays: str = Field(
        default="FR",
        min_length=2,
        max_length=2,
        description="Code pays ISO 3166-1 alpha-2 (ex: FR, TG, US)",
        examples=["FR", "TG", "US"],
    )

    @field_validator("pays")
    @classmethod
    def pays_en_majuscules(cls, v: str) -> str:
        return v.upper()

    @field_validator("ville")
    @classmethod
    def ville_nettoyee(cls, v: str) -> str:
        return v.strip()


class DonneesMeteo(BaseModel):
    """Données météo brutes d'un fournisseur, toutes unités normalisées."""

    temperature_c: float = Field(
        ...,
        ge=-90.0,
        le=60.0,
        description="Température en degrés Celsius",
    )
    humidite_pct: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Humidité relative en pourcentage",
    )
    vent_kmh: float = Field(
        ...,
        ge=0.0,
        le=500.0,
        description="Vitesse du vent en km/h",
    )
    description: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Description textuelle du temps (ex: Ensoleillé)",
    )
    # Codes OpenWeather utilisés comme référence inter-providers : 800 = ciel dégagé
    code_meteo: int = Field(
        default=0,
        ge=0,
        description="Code météo normalisé (référence OpenWeather)",
    )


class ResultatFournisseur(BaseModel):
    """Données d'un fournisseur avec métadonnées. Stocké en cache L1 (5 min)."""

    fournisseur: Literal["openweather", "open_meteo", "weatherapi"] = Field(
        ...,
        description="Identifiant du fournisseur météo",
    )
    donnees: DonneesMeteo = Field(..., description="Données météo normalisées")
    recupere_a: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Date/heure de récupération (UTC)",
    )
    depuis_cache: bool = Field(default=False)


class MeteoResponse(BaseModel):
    """Réponse consolidée de GET /meteo. Stockée en cache L2 (1 h)."""

    ville: str = Field(..., description="Nom de la ville demandée")
    pays: str = Field(..., description="Code pays (ex: FR)")
    temperature_c: float = Field(
        ..., description="Température moyenne des fournisseurs (°C)"
    )
    humidite_pct: float = Field(..., description="Humidité relative moyenne (%)")
    vent_kmh: float = Field(..., description="Vitesse du vent moyenne (km/h)")
    description: str = Field(
        ..., description="Description choisie par vote majoritaire"
    )
    fournisseurs_ok: list[str] = Field(default_factory=list)
    fournisseurs_ko: list[str] = Field(default_factory=list)
    nb_sources: int = Field(..., ge=1)
    depuis_cache: bool = Field(default=False)
    depuis_cache_l1: bool = Field(default=False)
    genere_a: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # 50 si 1 source, 100 si consensus parfait, 0 si forte divergence
    indice_confiance: int = Field(default=100, ge=0, le=100)
    avertissement: str | None = Field(default=None)

    @model_validator(mode="after")
    def verifier_coherence_sources(self):
        if self.nb_sources != len(self.fournisseurs_ok):
            raise ValueError(
                f"Incohérence : nb_sources={self.nb_sources} "
                f"!= len(fournisseurs_ok)={len(self.fournisseurs_ok)}"
            )
        return self


class DonneesComparaison(BaseModel):
    """Données brutes d'un fournisseur pour la comparaison inter-sources."""

    temperature_c: float
    humidite_pct: float
    vent_kmh: float
    description: str
    depuis_cache: bool


class ComparaisonResponse(BaseModel):
    """Réponse de GET /comparer — données brutes côte à côte avec écarts."""

    ville: str
    pays: str
    sources: dict[str, DonneesComparaison] = Field(default_factory=dict)
    ecarts: dict[str, float] = Field(default_factory=dict)
    indice_consensus: int = Field(..., ge=0, le=100)
    fournisseurs_ok: list[str] = Field(default_factory=list)
    fournisseurs_ko: list[str] = Field(default_factory=list)
    genere_a: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EtatCircuitBreaker(BaseModel):
    """État d'un circuit breaker pour un fournisseur donné (retourné par /sante)."""

    fournisseur: str = Field(..., description="Nom du fournisseur météo")
    etat: Literal["CLOSED", "OPEN", "HALF_OPEN"] = Field(...)
    nb_erreurs: int = Field(default=0, ge=0)
    dernier_succes: datetime | None = Field(default=None)


class EntreeHistorique(BaseModel):
    """Une entrée dans l'historique de températures d'une ville."""

    timestamp: str
    temperature_c: float
    description: str
    humidite_pct: float
    vent_kmh: float


class HistoriqueResponse(BaseModel):
    """Historique de températures d'une ville — retourné par GET /historique."""

    ville: str
    pays: str
    entrees: list[EntreeHistorique] = Field(default_factory=list)
    tendance: str = Field(default="stable")
    temp_min: float | None = Field(default=None)
    temp_max: float | None = Field(default=None)
    nb_entrees: int = Field(default=0)
    genere_a: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class VillePopulaire(BaseModel):
    """Une ville dans le classement de popularité."""

    rang: int = Field(..., ge=1)
    ville: str
    pays: str
    nb_requetes: int = Field(..., ge=0)


class VillesPopulairesResponse(BaseModel):
    """Classement des villes les plus consultées — GET /villes-populaires."""

    villes: list[VillePopulaire] = Field(default_factory=list)
    total_requetes: int = Field(default=0)
    genere_a: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AlerteMeteo(BaseModel):
    """Une alerte météo active."""

    type_alerte: str = Field(..., description="canicule | gel | vent_fort")
    niveau: str = Field(
        default="vigilance", description="danger | vigilance | information"
    )
    ville: str
    pays: str
    valeur: float
    seuil: float
    message: str
    declenchee_a: str


class AlertesResponse(BaseModel):
    """Réponse de GET /alertes — liste des alertes actives."""

    alertes: list[AlerteMeteo] = Field(default_factory=list)
    nb_actives: int = Field(default=0)
    genere_a: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class JourPrevision(BaseModel):
    """Prévision météo pour un jour donné."""

    date: str
    temp_min: float
    temp_max: float
    description: str
    code_meteo: int = Field(default=0)
    precipitation_mm: float = Field(default=0.0)


class PrevisionsResponse(BaseModel):
    """Prévisions 7 jours — retourné par GET /previsions."""

    ville: str
    pays: str
    jours: list[JourPrevision] = Field(default_factory=list)
    genere_a: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class VilleBatch(BaseModel):
    """Une ville dans une requête batch."""

    ville: str = Field(..., min_length=1, max_length=100)
    pays: str = Field(default="FR", min_length=2, max_length=2)

    @field_validator("pays")
    @classmethod
    def pays_upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("ville")
    @classmethod
    def ville_strip(cls, v: str) -> str:
        return v.strip()


class BatchRequest(BaseModel):
    """Corps d'une requête POST /batch."""

    villes: list[VilleBatch] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Liste de 1 à 10 villes à interroger en parallèle",
    )


class ResultatBatch(BaseModel):
    """Résultat pour une ville dans une réponse batch."""

    ville: str
    pays: str
    succes: bool
    donnees: MeteoResponse | None = None
    erreur: str | None = None


class BatchResponse(BaseModel):
    """Réponse de POST /batch."""

    resultats: list[ResultatBatch]
    nb_succes: int
    nb_erreurs: int
    genere_a: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SanteResponse(BaseModel):
    """Réponse de GET /sante — état global de l'application."""

    status: Literal["ok", "dégradé", "critique"]
    fournisseurs: list[EtatCircuitBreaker]
    cache_hits: int = Field(default=0, ge=0)
    cache_misses: int = Field(default=0, ge=0)
    verifie_a: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
