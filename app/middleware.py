import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app import cache as cache_module
from app.config import RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW

logger = logging.getLogger(__name__)

_CHEMINS_EXCLUS = frozenset({
    "/metrics", "/sante", "/dashboard", "/interface",
    "/docs", "/openapi.json", "/redoc", "/",
})

# Swagger/ReDoc injectent des scripts inline incompatibles avec le CSP strict
_CHEMINS_SANS_CSP = frozenset({"/docs", "/redoc", "/openapi.json"})

_HEADERS_SECURITE = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(self), camera=(), microphone=()",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    # 'unsafe-inline' limité aux styles : Leaflet en a besoin pour les markers
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' https://unpkg.com; "
        "style-src 'self' https://unpkg.com 'unsafe-inline'; "
        "img-src 'self' https://*.basemaps.cartocdn.com "
        "https://*.tile.openstreetmap.org data: blob:; "
        "connect-src 'self' "
        "https://geocoding-api.open-meteo.com "
        "https://nominatim.openstreetmap.org "
        "wss: ws:; "
        "font-src 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self';"
    ),
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting par IP (Redis INCR+EXPIRE) + headers de sécurité HTTP."""

    async def dispatch(self, request: Request, call_next):
        chemin = request.url.path

        if any(chemin.startswith(c) for c in _CHEMINS_EXCLUS):
            response = await call_next(request)
            self._ajouter_headers_securite(response, skip_csp=chemin in _CHEMINS_SANS_CSP)
            return response

        ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if not ip:
            ip = request.client.host if request.client else "unknown"

        autorise, nb_requetes = cache_module.verifier_rate_limit(
            ip, RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW
        )

        if not autorise:
            logger.warning("Rate limit dépassé — IP %s (%d req)", ip, nb_requetes)
            return JSONResponse(
                status_code=429,
                content={
                    "erreur": "Trop de requêtes",
                    "detail": (
                        f"Limite de {RATE_LIMIT_REQUESTS} requêtes "
                        f"par {RATE_LIMIT_WINDOW}s atteinte."
                    ),
                    "retry_after": RATE_LIMIT_WINDOW,
                },
                headers={
                    "Retry-After": str(RATE_LIMIT_WINDOW),
                    "X-RateLimit-Limit": str(RATE_LIMIT_REQUESTS),
                    "X-RateLimit-Remaining": "0",
                    **_HEADERS_SECURITE,
                },
            )

        response = await call_next(request)

        restant = max(0, RATE_LIMIT_REQUESTS - nb_requetes)
        response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_REQUESTS)
        response.headers["X-RateLimit-Remaining"] = str(restant)
        self._ajouter_headers_securite(response)

        return response

    @staticmethod
    def _ajouter_headers_securite(response, skip_csp: bool = False) -> None:
        for nom, valeur in _HEADERS_SECURITE.items():
            if skip_csp and nom == "Content-Security-Policy":
                continue
            response.headers.setdefault(nom, valeur)
