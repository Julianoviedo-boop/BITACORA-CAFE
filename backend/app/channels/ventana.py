"""Ventana de atención de 24 horas de WhatsApp.

WhatsApp solo deja mandar mensajes libres (texto normal) durante las 24 horas
siguientes al último mensaje del usuario. Fuera de esa ventana solo se pueden
mandar plantillas aprobadas por Meta.

Hoy toda respuesta de la conversación contesta un mensaje recién llegado, así
que casi siempre está dentro de la ventana. Puede quedar por fuera si el worker
estuvo caído o si un mensaje se reintentó por mucho tiempo.

Las alertas (app/domain/alertas.py) sí salen fuera de la ventana: usan
`Channel.send_template` con la plantilla de config/alertas.yaml o, si no hay,
quedan solo en el panel.

PENDIENTE: mandar con plantilla lo que quedó en `conversations.unsent_reply`
(respuestas de la conversación que no salieron por estar fuera de la ventana).
"""

from datetime import datetime, timedelta

VENTANA_ATENCION = timedelta(hours=24)


def dentro_de_ventana(ultimo_entrante: datetime | None, ahora: datetime) -> bool:
    """True si todavía se puede mandar un texto libre al usuario.

    `ultimo_entrante` es la hora del último mensaje que el usuario nos mandó.
    Sin mensajes del usuario no hay ventana abierta.
    """
    if ultimo_entrante is None:
        return False
    if ultimo_entrante.tzinfo is None or ahora.tzinfo is None:
        raise ValueError("Las horas de la ventana deben traer zona horaria")
    return ahora - ultimo_entrante < VENTANA_ATENCION
