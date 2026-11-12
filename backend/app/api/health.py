"""Endpoint de salud: comprueba base de datos y Redis."""

import logging
from collections.abc import Callable
from functools import lru_cache

import redis
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.db.session import get_engine

logger = logging.getLogger(__name__)
router = APIRouter(tags=["salud"])


@lru_cache
def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(
        get_settings().redis_url, socket_timeout=2, socket_connect_timeout=2
    )


def check_db() -> None:
    # Consulta técnica de conectividad: no toca tablas de negocio.
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))


def check_redis() -> None:
    _redis_client().ping()


def _run(check: Callable[[], None]) -> str:
    try:
        check()
        return "ok"
    except Exception as exc:  # noqa: BLE001 - cualquier fallo marca el servicio como caído
        logger.warning("Falla en chequeo de salud %s: %s", check.__name__, exc)
        return "error"


@router.get("/health")
def health() -> JSONResponse:
    # Se buscan las funciones en el módulo en cada llamada para poder sustituirlas en pruebas.
    checks = {"db": _run(check_db), "redis": _run(check_redis)}
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "error", "checks": checks},
    )
