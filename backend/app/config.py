"""Configuración leída exclusivamente de variables de entorno (.env)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_ignore_empty: una variable vacía en el .env (FOO=) usa el valor por defecto.
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)

    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://bitacora:bitacora@db:5432/bitacora_cafe"
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"

    # Carpeta con eventos.yaml, alertas.yaml, consentimiento.yaml y vocabulario.txt
    config_dir: str = "/config"

    # Cada cuántos minutos Celery beat revisa las alertas por tiempo (config/alertas.yaml).
    alertas_revision_minutos: int = Field(default=30, ge=1, le=24 * 60)

    # --- Canal de WhatsApp ---
    # "openbsp" habla con OpenBSP de verdad; "fake" no envía nada (desarrollo y pruebas).
    whatsapp_channel: Literal["openbsp", "fake"] = "fake"

    # OpenBSP (ver docs/decisiones.md, D-013)
    openbsp_base_url: str = ""
    # Header `apikey`: llave pública (publishable) de Supabase.
    openbsp_publishable_key: str = ""
    # Header `api-key`: llave secreta de la organización en OpenBSP.
    openbsp_api_key: str = ""
    # Id de NUESTRA organización dentro de OpenBSP (va en cada mensaje que se envía).
    openbsp_organization_id: str = ""
    # Secreto largo que va en la URL del webhook: /webhooks/openbsp/<secreto>.
    openbsp_webhook_secret: str = ""
    # Opcional: el `token` del webhook en OpenBSP; llega como `Authorization: Bearer <token>`.
    openbsp_webhook_token: str = ""

    # Canal falso: carpeta de donde se leen los audios/imágenes "fixture://<nombre>".
    fake_media_dir: str = "tests/fixtures"

    # --- Panel web (ver docs/decisiones.md, D-020) ---
    # Token que manda el servidor del panel en `Authorization: Bearer` (mínimo 32
    # caracteres). Vacío = los endpoints /panel responden 503.
    panel_api_token: str = ""
    # Organización que ve el panel. El token sirve solo para esta organización.
    panel_organization_id: str = ""
    # Dirección del panel en internet, sin "/" al final. El QR del reporte lleva a
    # <PUBLIC_WEB_URL>/l/<token> (la página pública del lote, D-023).
    public_web_url: str = "http://localhost:3000"

    # --- Transcripción (faster-whisper) ---
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_language: str = "es"
    # Hilos por proceso del worker; 0 = lo que decida CTranslate2. Con varios procesos
    # (--concurrency) conviene que hilos x procesos no pase de los núcleos.
    whisper_cpu_threads: int = 0

    # --- Extracción (API de Claude) ---
    # La llave va en ANTHROPIC_API_KEY y la lee directamente el SDK de Anthropic.
    anthropic_model: str = "claude-haiku-5-5"
    # Cuánto piensa el modelo antes de responder: low | medium | high | xhigh | max.
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"


@lru_cache
def get_settings() -> Settings:
    return Settings()
