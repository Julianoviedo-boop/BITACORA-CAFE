import logging

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.webhooks import OcultarSecretoWebhook
from app.api.webhooks import router as webhooks_router
from app.config import get_settings

logging.getLogger("uvicorn.access").addFilter(OcultarSecretoWebhook())

if get_settings().app_env == "production" and get_settings().whatsapp_channel == "fake":
    logging.getLogger(__name__).warning(
        "WHATSAPP_CHANNEL=fake en producción: no se enviará ni se descargará nada de WhatsApp"
    )

app = FastAPI(title="Bitácora de Café")
app.include_router(health_router)
app.include_router(webhooks_router)
