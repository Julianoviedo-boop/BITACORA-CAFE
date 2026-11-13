# syntax=docker/dockerfile:1

# ---------- base: python slim + usuario no root ----------
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/home/app/.cache/huggingface
# La carpeta de caché existe en la imagen con dueño "app" para que el volumen
# nombrado herede esos permisos la primera vez que se monta.
RUN groupadd --system --gid 1000 app \
 && useradd --system --uid 1000 --gid app --create-home --home-dir /home/app app \
 && mkdir -p /home/app/.cache/huggingface \
 && chown -R app:app /home/app
WORKDIR /app

# ---------- builder: dependencias de producción en un venv ----------
FROM base AS builder
COPY pyproject.toml ./
RUN python -m venv /opt/venv \
 && python -c "import tomllib; d = tomllib.load(open('pyproject.toml', 'rb')); print(chr(10).join(d['project']['dependencies']))" > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt

# ---------- builder-dev: + dependencias de desarrollo (pytest) ----------
FROM builder AS builder-dev
RUN python -c "import tomllib; d = tomllib.load(open('pyproject.toml', 'rb')); print(chr(10).join(d['project']['optional-dependencies']['dev']))" > /tmp/requirements-dev.txt \
 && pip install -r /tmp/requirements-dev.txt

# ---------- runtime: base común de api y worker ----------
FROM base AS runtime
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app pyproject.toml ./
COPY --chown=app:app app ./app
# Las migraciones van en la imagen: la api las aplica al arrancar y /health las verifica.
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic
USER app

# ---------- api (producción, sin recarga) ----------
# Aplica las migraciones pendientes y luego arranca; si fallan, no arranca.
# Con varias réplicas de la api, mejor correr las migraciones en un paso aparte.
FROM runtime AS api
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]

# ---------- worker (Celery) ----------
FROM runtime AS worker
CMD ["celery", "-A", "app.worker.celery_app:celery_app", "worker", "--loglevel=INFO", "--concurrency=2"]

# ---------- dev: api con pytest; el código se monta desde el host ----------
FROM base AS dev
COPY --from=builder-dev /opt/venv /opt/venv
COPY --chown=app:app . .
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
