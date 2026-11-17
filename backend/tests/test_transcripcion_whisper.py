"""app/transcription/whisper.py: vocabulario, decodificación real de OGG/Opus,
parámetros de transcribe() y caché del modelo.

La transcripción de verdad (bajar el modelo y correrlo) está marcada `whisper` y
solo corre con RUN_WHISPER_TESTS=1, porque tarda y necesita el modelo.
"""

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from app.config import Settings
from app.transcription import whisper
from app.transcription.whisper import (
    MAX_PROMPT_CHARS,
    AudioDemasiadoLargoError,
    AudioInvalidoError,
    cargar_vocabulario,
    confianza_media,
    construir_prompt,
    decodificar,
    transcribir,
)

FIXTURES = Path(__file__).parent / "fixtures"
AUDIO = FIXTURES / "sintetico_01_abonada.ogg"
_CONFIG = [Path(__file__).resolve().parents[2] / "config", Path("/config")]


# ---------------------------------------------------------------------------
# Dobles del modelo
# ---------------------------------------------------------------------------


@dataclass
class Seg:
    start: float
    end: float
    text: str
    avg_logprob: float = -0.1


class ModeloFalso:
    def __init__(self, segmentos):
        self.segmentos = segmentos
        self.llamadas = []
        self.consumidos = 0

    def transcribe(self, audio, **kwargs):
        self.llamadas.append((audio, kwargs))

        def gen():
            for s in self.segmentos:
                self.consumidos += 1
                yield s

        return gen(), object()


# ---------------------------------------------------------------------------
# Vocabulario
# ---------------------------------------------------------------------------


def test_cargar_vocabulario_ignora_comentarios_vacias_y_repetidos(tmp_path):
    p = tmp_path / "vocabulario.txt"
    p.write_text("# comentario\n\n  broca \nroya\nbroca\n# otro\nCastillo\n", encoding="utf-8")
    assert cargar_vocabulario(p) == ["broca", "roya", "Castillo"]


def test_cargar_vocabulario_sin_archivo(tmp_path):
    assert cargar_vocabulario(tmp_path / "no-existe.txt") == []


def test_prompt_vacio_es_none():
    assert construir_prompt([]) is None


def test_prompt_es_una_frase_con_los_terminos():
    assert construir_prompt(["broca", "roya"]) == "Bitácora de café: broca, roya."


def test_prompt_se_corta_si_es_muy_largo():
    prompt = construir_prompt([f"termino{i}" for i in range(500)], max_chars=100)
    assert len(prompt) <= 100 and prompt.endswith(".") and "termino0" in prompt


def test_vocabulario_del_repositorio_cabe_en_el_prompt():
    config_dir = next((p for p in _CONFIG if (p / "vocabulario.txt").is_file()), None)
    if config_dir is None:
        pytest.skip("No se encontró config/vocabulario.txt")
    terminos = cargar_vocabulario(config_dir / "vocabulario.txt")
    prompt = construir_prompt(terminos)
    assert terminos and "broca" in terminos
    assert prompt is not None and len(prompt) <= MAX_PROMPT_CHARS
    assert all(t in prompt for t in terminos), "Algún término no entró en el prompt"


# ---------------------------------------------------------------------------
# Decodificación (archivo real OGG/Opus como los de WhatsApp)
# ---------------------------------------------------------------------------


def test_decodifica_ogg_opus_real():
    audio = decodificar(AUDIO)
    assert audio.dtype == np.float32 and audio.ndim == 1
    assert 10.0 < len(audio) / whisper.SAMPLE_RATE < 10.7  # ffprobe: 10,34 s
    assert np.abs(audio).max() > 0.05  # tiene voz, no es silencio


@pytest.mark.parametrize("contenido", [b"", b"esto no es un audio", b"OggS" + b"\x00" * 200,
                                       b"\x89PNG\r\n\x1a\n" + b"\x00" * 100])
def test_archivo_invalido(tmp_path, contenido):
    p = tmp_path / "nota.ogg"
    p.write_bytes(contenido)
    with pytest.raises(AudioInvalidoError):
        decodificar(p)


def test_ogg_cortado_a_la_mitad_decodifica_lo_que_hay(tmp_path):
    # WhatsApp a veces entrega audios incompletos si la descarga se corta.
    p = tmp_path / "cortado.ogg"
    data = AUDIO.read_bytes()
    p.write_bytes(data[: len(data) // 2])
    assert 2 < len(decodificar(p)) / whisper.SAMPLE_RATE < 8


# ---------------------------------------------------------------------------
# transcribir() con un modelo falso
# ---------------------------------------------------------------------------


def test_transcribe_con_los_parametros_pedidos_y_consume_todo():
    modelo = ModeloFalso([Seg(0, 4, " Hoy abonamos "), Seg(4, 6, " "), Seg(6, 10, "el lote.")])
    r = transcribir(AUDIO, max_duracion_s=180, modelo=modelo, prompt="Bitácora de café: broca.",
                    language="es")

    (audio, kwargs), = modelo.llamadas
    assert isinstance(audio, np.ndarray)  # se decodifica una sola vez y se pasa el arreglo
    assert kwargs == {"language": "es", "vad_filter": True,
                      "initial_prompt": "Bitácora de café: broca."}
    assert modelo.consumidos == 3
    assert r.texto == "Hoy abonamos el lote."
    assert 10.0 < r.duracion_s < 10.7
    assert set(r.tiempos) == {"decodificar", "transcribir"}


def test_idioma_y_prompt_por_defecto_salen_de_la_configuracion(monkeypatch):
    monkeypatch.setattr(whisper, "get_settings", lambda: Settings(whisper_language="es"))
    monkeypatch.setattr(whisper, "prompt_desde_config", lambda: "pista")
    modelo = ModeloFalso([Seg(0, 1, "hola")])
    transcribir(AUDIO, max_duracion_s=180, modelo=modelo)
    kwargs = modelo.llamadas[0][1]
    assert kwargs["language"] == "es" and kwargs["initial_prompt"] == "pista"


def test_rechaza_audio_largo_sin_transcribir():
    modelo = ModeloFalso([Seg(0, 1, "no debería")])
    with pytest.raises(AudioDemasiadoLargoError) as exc:
        transcribir(AUDIO, max_duracion_s=5, modelo=modelo)
    assert modelo.llamadas == []
    # Se detiene apenas pasa del máximo: no decodifica los 10 s completos.
    assert exc.value.maximo_s == 5 and 5 < exc.value.duracion_s < 7


def test_decodificar_se_detiene_apenas_pasa_el_maximo():
    # El audio dura 10,34 s. Con máximo de 1 s debe parar cerca de 1 s, no leer
    # todo: así un audio de horas en pocos MB no llena la memoria del worker.
    with pytest.raises(AudioDemasiadoLargoError) as exc:
        decodificar(AUDIO, max_duracion_s=1)
    assert 1 < exc.value.duracion_s < 2


def test_decodificar_sin_maximo_lee_todo():
    assert len(decodificar(AUDIO, max_duracion_s=None)) / whisper.SAMPLE_RATE > 10


def test_audio_justo_en_el_maximo_pasa():
    audio = decodificar(AUDIO, max_duracion_s=11)
    assert 10 < len(audio) / whisper.SAMPLE_RATE < 11


def test_audio_sin_voz_da_texto_vacio():
    r = transcribir(AUDIO, max_duracion_s=180, modelo=ModeloFalso([]), prompt=None)
    assert r.texto == "" and r.confianza == 0.0


def test_confianza_media_ponderada_por_duracion():
    import math

    segs = [Seg(0, 9, "a", avg_logprob=math.log(0.9)), Seg(9, 10, "b", avg_logprob=math.log(0.4))]
    assert confianza_media(segs) == pytest.approx(0.85, abs=0.001)
    assert confianza_media([]) == 0.0
    assert confianza_media([Seg(0, 1, "x", avg_logprob=0.3)]) == 1.0  # nunca pasa de 1


# ---------------------------------------------------------------------------
# Modelo: CPU int8, configurable y cargado una sola vez
# ---------------------------------------------------------------------------


def test_configuracion_por_defecto(monkeypatch):
    for var in ("WHISPER_MODEL", "WHISPER_DEVICE", "WHISPER_COMPUTE_TYPE", "WHISPER_LANGUAGE"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert (s.whisper_model, s.whisper_device, s.whisper_compute_type, s.whisper_language) == \
        ("small", "cpu", "int8", "es")


def test_modelo_configurable_por_variable_de_entorno(monkeypatch):
    monkeypatch.setenv("WHISPER_MODEL", "medium")
    assert Settings(_env_file=None).whisper_model == "medium"


def test_modelo_se_carga_una_sola_vez(monkeypatch):
    import faster_whisper

    creados = []

    class WhisperModelFalso:
        def __init__(self, *args, **kwargs):
            creados.append((args, kwargs))

    monkeypatch.setattr(faster_whisper, "WhisperModel", WhisperModelFalso)
    monkeypatch.setattr(whisper, "get_settings", lambda: Settings(whisper_model="small"))
    whisper.get_modelo.cache_clear()
    try:
        a = whisper.modelo_desde_settings()
        b = whisper.modelo_desde_settings()
    finally:
        whisper.get_modelo.cache_clear()
    assert a is b
    assert creados == [(("small",), {"device": "cpu", "compute_type": "int8", "cpu_threads": 0})]


# ---------------------------------------------------------------------------
# Integración con el modelo real (lenta)
# ---------------------------------------------------------------------------


@pytest.mark.whisper
@pytest.mark.skipif(os.environ.get("RUN_WHISPER_TESTS") != "1",
                    reason="Transcripción real: correr con RUN_WHISPER_TESTS=1")
def test_transcripcion_real_del_ogg():
    r = transcribir(AUDIO, max_duracion_s=180)
    texto = r.texto.lower()
    assert "abonamos" in texto and "lote" in texto
    assert 0.3 < r.confianza <= 1.0
