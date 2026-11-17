"""Webhook de OpenBSP: autentica, guarda, encola y responde enseguida.

OpenBSP no firma sus webhooks (docs/decisiones.md, D-013). La autenticación es:
1. un secreto largo en la URL (/webhooks/openbsp/<OPENBSP_WEBHOOK_SECRET>),
   comparado en tiempo constante; y
2. si se configuró OPENBSP_WEBHOOK_TOKEN, también el header
   `Authorization: Bearer <token>` que OpenBSP manda cuando el webhook tiene token.

OpenBSP envía el aviso una sola vez y no reintenta (pg_net), así que este
endpoint responde 200 a todo aviso auténtico, aunque lo ignore.
"""

import hmac
import json
import logging
import re
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.channels import Channel, get_channel
from app.config import Settings, get_settings
from app.db.repositories import Repositories, unit_of_work
from app.domain.mensajes import registrar_mensaje_entrante

logger = logging.getLogger(__name__)
router = APIRouter(tags=["webhooks"])

MIN_SECRET_LENGTH = 32

UnitOfWork = Callable[[], AbstractContextManager[Repositories]]
Encolar = Callable[[uuid.UUID, uuid.UUID], None]


# --- autenticación ---


def _iguales(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def es_produccion(settings: Settings) -> bool:
    return settings.app_env.strip().lower() == "production"


def problemas_de_credenciales(settings: Settings) -> list[str]:
    """Qué le falta a la configuración del webhook. Vacío = todo bien.

    En producción se exigen las DOS credenciales: el secreto de la URL y el token
    del header. La URL queda escrita en la configuración de OpenBSP, en logs de
    proxies y en capturas de pantalla; si solo se pidiera eso, quien la viera
    podría hacerse pasar por cualquier productor y confirmar sus eventos
    (regla de oro 2). El token viaja aparte, en el header Authorization.
    """
    problemas = []
    if len(settings.openbsp_webhook_secret) < MIN_SECRET_LENGTH:
        problemas.append(f"OPENBSP_WEBHOOK_SECRET vacío o con menos de {MIN_SECRET_LENGTH} caracteres")
    if es_produccion(settings) and len(settings.openbsp_webhook_token) < MIN_SECRET_LENGTH:
        problemas.append(
            f"OPENBSP_WEBHOOK_TOKEN vacío o con menos de {MIN_SECRET_LENGTH} caracteres "
            "(obligatorio con APP_ENV=production)"
        )
    return problemas


def validar_al_arrancar(settings: Settings | None = None) -> None:
    """En producción la API no arranca si al webhook le falta una credencial."""
    settings = settings or get_settings()
    problemas = problemas_de_credenciales(settings)
    if problemas and es_produccion(settings):
        raise RuntimeError("Webhook mal configurado: " + "; ".join(problemas))


def autenticado(secreto_url: str, authorization: str | None, settings: Settings) -> bool:
    problemas = problemas_de_credenciales(settings)
    if problemas:
        logger.error("Se rechazan todos los webhooks: %s", "; ".join(problemas))
        return False
    ok = _iguales(secreto_url, settings.openbsp_webhook_secret)
    if settings.openbsp_webhook_token:
        auth = authorization or ""
        recibido = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        ok = _iguales(recibido, settings.openbsp_webhook_token) and ok
    return ok


class OcultarSecretoWebhook(logging.Filter):
    """Evita que el secreto de la URL quede en los logs de acceso de uvicorn."""

    _patron = re.compile(r"(/webhooks/openbsp/)[^/?\s\"]+")

    def _ocultar(self, valor: Any) -> Any:
        return self._patron.sub(r"\1***", valor) if isinstance(valor, str) else valor

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._ocultar(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self._ocultar(a) for a in record.args)
        return True


# --- dependencias (se sustituyen en las pruebas) ---


def get_unit_of_work() -> UnitOfWork:
    return unit_of_work


def _encolar_en_celery(organization_id: uuid.UUID, message_id: uuid.UUID) -> None:
    from app.worker.tasks import procesar_mensaje

    # Pocos reintentos y cortos: el webhook no debe quedarse esperando a Redis.
    procesar_mensaje.apply_async(
        args=[str(organization_id), str(message_id)],
        retry=True,
        retry_policy={"max_retries": 2, "interval_start": 0, "interval_step": 0.2,
                      "interval_max": 0.5},
    )


def get_encolar() -> Encolar:
    return _encolar_en_celery


# --- endpoint ---


def _procesar(payload: Any, channel: Channel, uow: UnitOfWork, encolar: Encolar) -> str:
    incoming = channel.parse_incoming(payload)
    if incoming is None:
        return "ignorado"  # mensaje propio, cambio de estado, grupo, otra tabla...

    # El commit ocurre al salir del bloque: el worker siempre encuentra el mensaje.
    with uow() as repos:
        registro = registrar_mensaje_entrante(repos, incoming)

    if registro.resultado == "numero_desconocido":
        logger.warning("Mensaje para un número que no es de ninguna organización: %s",
                       incoming.channel_address)
    elif registro.resultado == "remitente_no_registrado":
        logger.info("Mensaje de un teléfono no registrado (…%s) en la organización %s",
                    incoming.sender_phone[-4:], registro.organization_id)
    elif registro.resultado == "guardado":
        try:
            encolar(registro.organization_id, registro.message_id)
        except Exception:  # noqa: BLE001 - el mensaje queda guardado en "recibido"
            logger.exception("No se pudo encolar el mensaje %s", registro.message_id)
    return registro.resultado


@router.post("/webhooks/openbsp/{secreto}")
async def webhook_openbsp(
    secreto: str,
    request: Request,
    channel: Channel = Depends(get_channel),
    uow: UnitOfWork = Depends(get_unit_of_work),
    encolar: Encolar = Depends(get_encolar),
) -> JSONResponse:
    # Se autentica antes de leer el cuerpo: a quien no tiene el secreto no se le
    # dice nada sobre el formato esperado.
    if not autenticado(secreto, request.headers.get("authorization"), get_settings()):
        return JSONResponse(status_code=401, content={"detail": "No autorizado"})
    try:
        payload = json.loads(await request.body())
    except ValueError:
        return JSONResponse(status_code=400, content={"detail": "El cuerpo no es JSON válido"})
    resultado = await run_in_threadpool(_procesar, payload, channel, uow, encolar)
    return JSONResponse(status_code=200, content={"resultado": resultado})
