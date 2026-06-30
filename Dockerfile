FROM python:3.11-slim AS builder

WORKDIR /app

# requirements.txt en premier pour profiter du cache Docker lors des rebuilds
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.11-slim AS runtime

# Utilisateur non-root : bonne pratique de sécurité pour les conteneurs
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

COPY --from=builder /install /usr/local
COPY app/ ./app/

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2"]
