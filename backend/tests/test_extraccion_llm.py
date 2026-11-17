"""Cliente de Claude: reintentos, errores definitivos y registro de tokens (sin red)."""

import logging
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from pydantic import BaseModel

from app.extraction.llm import ClienteLLM, ErrorLLM, es_temporal

_REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


class Salida(BaseModel):
    ok: bool


def _status(cls, codigo, headers=None):
    return cls("x", response=httpx2.Response(codigo, headers=headers or {}, request=_REQ), body=None)


def _respuesta(stop_reason="end_turn", parsed=Salida(ok=True)):
    return SimpleNamespace(
        stop_reason=stop_reason, parsed_output=parsed, model="claude-haiku-5-5",
        _request_id="req_1", stop_details=SimpleNamespace(category="cyber"),
        usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_creation_input_tokens=0,
                              cache_read_input_tokens=3000),
    )


class FakeClient:
    def __init__(self, *efectos):
        self.efectos = list(efectos)
        self.llamadas = []
        self.messages = self

    def parse(self, **kwargs):
        self.llamadas.append(kwargs)
        efecto = self.efectos.pop(0)
        if isinstance(efecto, Exception):
            raise efecto
        return efecto


def _cliente(fake, **kw):
    esperas = []
    c = ClienteLLM(fake, modelo="claude-haiku-5-5", effort="low", dormir=esperas.append,
                   espera_base_s=1.0, **kw)
    return c, esperas


def test_llamada_correcta_y_registro_de_tokens(caplog):
    fake = FakeClient(_respuesta())
    c, _ = _cliente(fake)
    with caplog.at_level(logging.INFO, logger="app.extraction.llm"):
        salida, uso = c.parse(system="reglas", mensaje="hola", output_format=Salida)
    assert salida.ok is True
    assert (uso.input_tokens, uso.output_tokens, uso.cache_read_input_tokens, uso.intentos) == (
        100, 20, 3000, 1)
    llamada = fake.llamadas[0]
    assert llamada["model"] == "claude-haiku-5-5"
    assert llamada["output_format"] is Salida
    assert llamada["output_config"] == {"effort": "low"}
    assert llamada["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert llamada["messages"] == [{"role": "user", "content": "hola"}]
    assert "entrada=100 salida=20" in caplog.text and "cache_leida=3000" in caplog.text


def test_reintenta_errores_temporales():
    fake = FakeClient(_status(anthropic.RateLimitError, 429, {"retry-after": "2"}),
                      _status(anthropic.InternalServerError, 500),
                      anthropic.APIConnectionError(request=_REQ),
                      _respuesta())
    c, esperas = _cliente(fake)
    _, uso = c.parse(system="s", mensaje="m", output_format=Salida)
    assert uso.intentos == 4
    assert esperas[0] == 2.0  # respeta retry-after
    assert 2.0 <= esperas[1] < 3.0  # exponencial: 2^1 + aleatorio


def test_no_reintenta_errores_definitivos():
    fake = FakeClient(_status(anthropic.BadRequestError, 400), _respuesta())
    c, esperas = _cliente(fake)
    with pytest.raises(ErrorLLM) as e:
        c.parse(system="s", mensaje="m", output_format=Salida)
    assert e.value.temporal is False and esperas == [] and len(fake.llamadas) == 1


def test_agota_los_intentos():
    fake = FakeClient(*[_status(anthropic.APIStatusError, 529) for _ in range(3)])
    c, esperas = _cliente(fake, max_intentos=3)
    with pytest.raises(ErrorLLM) as e:
        c.parse(system="s", mensaje="m", output_format=Salida)
    assert e.value.temporal is True and len(esperas) == 2


@pytest.mark.parametrize("respuesta, texto", [
    (_respuesta(stop_reason="refusal", parsed=None), "negó"),
    (_respuesta(stop_reason="max_tokens", parsed=None), "cortada"),
    (_respuesta(parsed=None), "sin salida"),
])
def test_respuestas_inservibles(respuesta, texto):
    c, _ = _cliente(FakeClient(respuesta))
    with pytest.raises(ErrorLLM, match=texto):
        c.parse(system="s", mensaje="m", output_format=Salida)


def test_clasificacion_de_errores():
    assert es_temporal(_status(anthropic.APIStatusError, 503))
    assert es_temporal(anthropic.APITimeoutError(request=_REQ))
    assert not es_temporal(_status(anthropic.AuthenticationError, 401))
    assert not es_temporal(ValueError())


# ---------------------------------------------------------------------------
# Techo de tokens por respuesta (config/limites.yaml -> max_tokens_respuesta)
# ---------------------------------------------------------------------------


def test_max_tokens_por_defecto_del_cliente():
    fake = FakeClient(_respuesta())
    c, _ = _cliente(fake, max_tokens=8000)
    c.parse(system="reglas", mensaje="hola", output_format=Salida)
    assert fake.llamadas[0]["max_tokens"] == 8000


def test_max_tokens_de_la_llamada_manda_sobre_el_del_cliente():
    fake = FakeClient(_respuesta())
    c, _ = _cliente(fake, max_tokens=8000)
    c.parse(system="reglas", mensaje="hola", output_format=Salida, max_tokens=500)
    assert fake.llamadas[0]["max_tokens"] == 500


def test_sin_configurar_sigue_en_16000():
    fake = FakeClient(_respuesta())
    c, _ = _cliente(fake)
    c.parse(system="reglas", mensaje="hola", output_format=Salida)
    assert fake.llamadas[0]["max_tokens"] == 16000
