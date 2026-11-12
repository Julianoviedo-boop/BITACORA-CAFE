"""Configuración leída exclusivamente de variables de entorno (.env)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://bitacora:bitacora@db:5432/bitacora_cafe"
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"

    # Carpeta con eventos.yaml, alertas.yaml y vocabulario.txt
    config_dir: str = "/config"


@lru_cache
def get_settings() -> Settings:
    return Settings()
