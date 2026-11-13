"""Transcripción de audios con faster-whisper, en CPU.

- El modelo se carga una sola vez por proceso (get_modelo guarda el resultado).
  En el worker, app.worker.tasks lo precarga al arrancar cada proceso hijo, así
  la primera tarea no paga la carga.
- El audio se decodifica una vez con PyAV (el ffmpeg que trae faster-whisper):
  sirve para OGG/Opus de WhatsApp, M4A, MP3 y WAV, y da la duración exacta
  aunque el archivo no la traiga en sus metadatos (los OGG de WhatsApp a veces no).
- Este módulo no sabe de mensajes, organizaciones ni WhatsApp: recibe un archivo
  y devuelve texto. El caso de uso está en app/domain/transcripcion.py.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np

from app.config import get_settings

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
# Whisper usa como pista unos 220 tokens; lo que sobre se corta. En español un
# token es ~3-4 caracteres, así que más de esto no sirve.
MAX_PROMPT_CHARS = 700


class TranscripcionError(Exception):
    """Base de los errores de transcripción."""


class AudioInvalidoError(TranscripcionError):
    """El archivo no es un audio que se pueda decodificar, o está vacío."""


class AudioDemasiadoLargoError(TranscripcionError):
    def __init__(self, duracion_s: float, maximo_s: float) -> None:
        super().__init__(f"El audio dura {duracion_s:.0f} s; el máximo es {maximo_s:.0f} s")
        self.duracion_s = duracion_s
        self.maximo_s = maximo_s


@dataclass(frozen=True)
class Transcripcion:
    texto: str
    duracion_s: float
    # Promedio de exp(avg_logprob) de los segmentos, ponderado por su duración (0 a 1).
    confianza: float
    # Segundos de cada paso: {"decodificar": 0.1, "transcribir": 8.3}
    tiempos: dict[str, float] = field(default_factory=dict)


class _Segmento(Protocol):
    start: float
    end: float
    text: str
    avg_logprob: float


# ---------------------------------------------------------------------------
# Vocabulario -> initial_prompt
# ---------------------------------------------------------------------------


def cargar_vocabulario(path: str | Path) -> list[str]:
    """Lee un término por línea; ignora vacías, comentarios (#) y repetidos."""
    p = Path(path)
    if not p.is_file():
        logger.warning("No existe %s: se transcribe sin vocabulario", p)
        return []
    vistos: dict[str, None] = {}
    for linea in p.read_text(encoding="utf-8").splitlines():
        termino = linea.strip()
        if termino and not termino.startswith("#"):
            vistos.setdefault(termino, None)
    return list(vistos)


def construir_prompt(terminos: Iterable[str], max_chars: int = MAX_PROMPT_CHARS) -> str | None:
    """Arma la pista para Whisper. Se escribe como una frase con puntuación porque
    Whisper imita el estilo de la pista: una lista sin puntos lo lleva a
    transcribir sin puntos."""
    terminos = list(terminos)
    if not terminos:
        return None
    partes: list[str] = []
    largo = len("Bitácora de café: .")
    for t in terminos:
        if largo + len(t) + 2 > max_chars:
            logger.warning("vocabulario.txt es muy largo: se usan los primeros %d términos",
                           len(partes))
            break
        partes.append(t)
        largo += len(t) + 2
    return f"Bitácora de café: {', '.join(partes)}."


@lru_cache
def prompt_desde_config() -> str | None:
    return construir_prompt(cargar_vocabulario(Path(get_settings().config_dir) / "vocabulario.txt"))


# ---------------------------------------------------------------------------
# Modelo (uno por proceso)
# ---------------------------------------------------------------------------


@lru_cache
def get_modelo(nombre: str, device: str, compute_type: str, cpu_threads: int) -> WhisperModel:
    """Carga el modelo la primera vez y lo reutiliza en el resto de tareas del proceso.

    Ojo: con el pool prefork de Celery, cada proceso hijo tiene su propia copia en
    memoria (small int8 ~ 0,5 GB; medium int8 ~ 1,5 GB).
    """
    from faster_whisper import WhisperModel

    t0 = time.perf_counter()
    modelo = WhisperModel(nombre, device=device, compute_type=compute_type,
                          cpu_threads=cpu_threads)
    logger.info("whisper: modelo %s (%s/%s) cargado en %.1f s", nombre, device, compute_type,
                time.perf_counter() - t0)
    return modelo


def modelo_desde_settings() -> WhisperModel:
    s = get_settings()
    return get_modelo(s.whisper_model, s.whisper_device, s.whisper_compute_type,
                      s.whisper_cpu_threads)


# ---------------------------------------------------------------------------
# Transcripción
# ---------------------------------------------------------------------------


def decodificar(path: str | Path) -> np.ndarray:
    """Audio mono float32 a 16 kHz. Lanza AudioInvalidoError si no se puede leer.

    No se usa faster_whisper.decode_audio: en faster-whisper 1.2.1 llama a
    av.open(metadata_errors=...), que PyAV 16+ ya no acepta (TypeError con
    cualquier audio). Aquí solo se usan funciones estables de PyAV.
    Los frames dañados se saltan (audios cortados a mitad de descarga).
    """
    import av

    trozos: list[np.ndarray] = []
    try:
        with av.open(str(path), mode="r") as contenedor:
            if not contenedor.streams.audio:
                raise AudioInvalidoError("El archivo no tiene audio")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
            frames = contenedor.decode(audio=0)
            while True:
                try:
                    frame = next(frames)
                except StopIteration:
                    break
                except av.error.InvalidDataError:
                    continue
                trozos.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(frame))
            trozos.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(None))
    except av.error.FFmpegError as exc:
        raise AudioInvalidoError(f"No se pudo decodificar el audio: {exc}") from exc
    if not trozos:
        raise AudioInvalidoError("El audio está vacío")
    return np.concatenate(trozos).astype(np.float32) / 32768.0


def confianza_media(segmentos: Iterable[_Segmento]) -> float:
    total = peso = 0.0
    for s in segmentos:
        dur = max(s.end - s.start, 0.01)
        total += math.exp(min(s.avg_logprob, 0.0)) * dur
        peso += dur
    return round(total / peso, 3) if peso else 0.0


def transcribir(
    path: str | Path,
    *,
    max_duracion_s: float,
    modelo: Any | None = None,
    prompt: str | None = None,
    language: str | None = None,
) -> Transcripcion:
    """Transcribe un archivo de audio.

    Lanza AudioInvalidoError si no se puede decodificar y AudioDemasiadoLargoError
    si dura más de max_duracion_s (antes de gastar CPU transcribiendo).
    `modelo`, `prompt` y `language` por defecto salen de la configuración.
    """
    tiempos: dict[str, float] = {}

    t0 = time.perf_counter()
    audio = decodificar(path)
    duracion = len(audio) / SAMPLE_RATE
    tiempos["decodificar"] = round(time.perf_counter() - t0, 3)

    if duracion > max_duracion_s:
        raise AudioDemasiadoLargoError(duracion, max_duracion_s)

    modelo = modelo if modelo is not None else modelo_desde_settings()
    t0 = time.perf_counter()
    segmentos_gen, _info = modelo.transcribe(
        audio,
        language=language or get_settings().whisper_language,
        vad_filter=True,
        initial_prompt=prompt if prompt is not None else prompt_desde_config(),
    )
    # transcribe() es perezoso: el trabajo ocurre al recorrer el generador.
    segmentos = list(segmentos_gen)
    tiempos["transcribir"] = round(time.perf_counter() - t0, 3)

    texto = " ".join(s.text.strip() for s in segmentos if s.text.strip())
    return Transcripcion(
        texto=texto,
        duracion_s=round(duracion, 2),
        confianza=confianza_media(segmentos),
        tiempos=tiempos,
    )
