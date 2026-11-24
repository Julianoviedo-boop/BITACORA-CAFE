import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.panel import router as panel_router
from app.api.publico import router as publico_router
from app.api.reportes import router as reportes_router
from app.api import webhooks
from app.api.webhooks import OcultarSecretoWebhook
from app.api.webhooks import router as webhooks_router
from app.config import get_settings
from app.domain import alertas

logging.getLogger("uvicorn.access").addFilter(OcultarSecretoWebhook())

if get_settings().app_env == "production" and get_settings().whatsapp_channel == "fake":
    logging.getLogger(__name__).warning(
        "WHATSAPP_CHANNEL=fake en producción: no se enviará ni se descargará nada de WhatsApp"
    )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Si config/alertas.yaml tiene errores, la API no arranca y el log dice cuáles.
    alertas.validar_al_arrancar()
    # En producción, sin las dos credenciales del webhook la API no arranca.
    webhooks.validar_al_arrancar()
    yield


app = FastAPI(title="Bitácora de Café", lifespan=lifespan)
app.include_router(health_router)
app.include_router(webhooks_router)
app.include_router(panel_router)
app.include_router(publico_router)
app.include_router(reportes_router)
