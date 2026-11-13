"""Llamada a Claude con salida estructurada, reintentos y registro de tokens.

- Usa client.messages.parse(output_format=<modelo Pydantic>): el SDK manda el
  esquema en output_config.format y valida la respuesta.
- Reintenta solo errores temporales (429, 5xx, 529 sobrecarga, red, timeout) con
  espera exponencial y respeta el header retry-after. Los reintentos internos del
  SDK se apagan (max_retries=0) para que haya un solo ciclo y quede en el log.
- Los errores definitivos (400, 401, 403, 404, negativa del modelo, respuesta
  cortada o que no valida) no se reintentan: suben como ErrorLLM.
- Cada llamada deja en el log modelo, tokens (incluida la caché), intentos y
  duración, y los devuelve en UsoTokens para que el que llama los guarde.

El modelo y el esfuerzo salen de variables de entorno (ANTHROPIC_MODEL,
ANTHROPIC_EFFORT); ver app/config.py.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import anthropic
import pydantic
from pydantic import BaseModel

from app.config import get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_ESTADOS_TEMPORALES = {408, 409, 429, 500, 502, 503, 504, 529}


class ErrorLLM(Exception):
    """La extracción no se pudo hacer. `temporal` dice si vale la pena reintentar luego."""

    def __init__(self, mensaje: str, *, temporal: bool = False) -> None:
        super().__init__(mensaje)
        self.temporal = temporal


@dataclass(frozen=True)
class UsoTokens:
    modelo: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    intentos: int
    duracion_s: float
    request_id: str | None

    def a_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def es_temporal(error: Exception) -> bool:
    if isinstance(error, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        return True
    if isinstance(error, anthropic.APIStatusError):
        return error.status_code in _ESTADOS_TEMPORALES or error.status_code >= 500
    return False


def _retry_after(error: Exception) -> float | None:
    response = getattr(error, "response", None)
    valor = response.headers.get("retry-after") if response is not None else None
    try:
        return float(valor) if valor is not None else None
    except ValueError:
        return None


class ClienteLLM:
    def __init__(
        self,
        client: Any | None = None,
        *,
        modelo: str | None = None,
        effort: str | None = None,
        max_intentos: int = 4,
        espera_base_s: float = 1.0,
        espera_max_s: float = 30.0,
        timeout_s: float = 60.0,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        settings = get_settings()
        self.modelo = modelo or settings.anthropic_model
        self.effort = effort or settings.anthropic_effort
        self.max_intentos = max(1, max_intentos)
        self.espera_base_s = espera_base_s
        self.espera_max_s = espera_max_s
        self._dormir = dormir
        # La llave sale de ANTHROPIC_API_KEY (la lee el SDK); nunca va en el código.
        self._client = client or anthropic.Anthropic(max_retries=0, timeout=timeout_s)

    def _espera(self, intento: int, error: Exception) -> float:
        sugerida = _retry_after(error)
        if sugerida is not None:
            return min(sugerida, self.espera_max_s)
        base = self.espera_base_s * (2 ** (intento - 1))
        return min(base + random.uniform(0, self.espera_base_s), self.espera_max_s)

    def parse(
        self,
        *,
        system: str,
        mensaje: str,
        output_format: type[T],
        max_tokens: int = 16000,
        etiqueta: str = "extraccion",
    ) -> tuple[T, UsoTokens]:
        inicio = time.monotonic()
        for intento in range(1, self.max_intentos + 1):
            try:
                response = self._client.messages.parse(
                    model=self.modelo,
                    max_tokens=max_tokens,
                    # El system es fijo por catálogo: se cachea (mismo prefijo en cada llamada).
                    system=[{"type": "text", "text": system,
                             "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": mensaje}],
                    output_format=output_format,
                    output_config={"effort": self.effort},
                )
                break
            except pydantic.ValidationError as e:
                raise ErrorLLM(f"La respuesta no cumple el esquema: {e}") from e
            except anthropic.APIError as e:
                if not es_temporal(e):
                    logger.error("%s: error definitivo de la API (%s): %s", etiqueta,
                                 type(e).__name__, e)
                    raise ErrorLLM(f"Error de la API: {e}") from e
                if intento == self.max_intentos:
                    logger.error("%s: error temporal tras %d intentos: %s", etiqueta,
                                 intento, e)
                    raise ErrorLLM(f"Error temporal de la API: {e}", temporal=True) from e
                espera = self._espera(intento, e)
                logger.warning("%s: error temporal (%s), intento %d de %d; espera %.1fs",
                               etiqueta, type(e).__name__, intento, self.max_intentos, espera)
                self._dormir(espera)

        usage = response.usage
        uso = UsoTokens(
            modelo=getattr(response, "model", self.modelo),
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            intentos=intento,
            duracion_s=round(time.monotonic() - inicio, 3),
            request_id=getattr(response, "_request_id", None),
        )
        logger.info(
            "%s: modelo=%s entrada=%d salida=%d cache_escrita=%d cache_leida=%d "
            "intentos=%d duracion=%.2fs request_id=%s",
            etiqueta, uso.modelo, uso.input_tokens, uso.output_tokens,
            uso.cache_creation_input_tokens, uso.cache_read_input_tokens, uso.intentos,
            uso.duracion_s, uso.request_id,
        )

        # Revisar por qué paró ANTES de leer el contenido.
        if response.stop_reason == "refusal":
            categoria = getattr(getattr(response, "stop_details", None), "category", None)
            raise ErrorLLM(f"El modelo se negó a responder (categoría: {categoria})")
        if response.stop_reason == "max_tokens":
            raise ErrorLLM("La respuesta quedó cortada (max_tokens)")
        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            raise ErrorLLM(f"Respuesta sin salida estructurada (stop_reason={response.stop_reason})")
        return parsed, uso
