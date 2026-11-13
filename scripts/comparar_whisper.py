"""Compara modelos de whisper (tiempo y errores) sobre audios con su texto de referencia.

Cada audio <nombre>.ogg (o .opus, .m4a, .mp3, .wav) necesita al lado un
<nombre>.txt con lo que de verdad se dijo. Se usa la misma función que el worker
(app.transcription.whisper.transcribir), con el mismo initial_prompt.

Uso (dentro del worker, que tiene faster-whisper y la caché de modelos):
    docker compose exec worker python /scripts/comparar_whisper.py
    docker compose exec worker python /scripts/comparar_whisper.py --modelos small medium --carpeta tests/fixtures

Cada modelo corre en un proceso aparte para medir su memoria sin mezclarla.
Antes de medir se hace una transcripción de calentamiento que no cuenta.

Métricas:
  WER       errores de palabra (sustituciones + borradas + insertadas) / palabras de la
            referencia. Se compara sin mayúsculas, tildes ni signos, y con los números
            pasados a cifras ("cuatro" = "4", "17-6-18" = "diecisiete seis dieciocho").
  Términos  términos de config/vocabulario.txt que están en la referencia y que el
            modelo escribió bien. Es lo que más le importa a la extracción.
  RTF       segundos de proceso / segundos de audio (0,25 = 4 veces más rápido que real).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))  # /app dentro del contenedor

EXTENSIONES = (".ogg", ".opus", ".m4a", ".mp3", ".wav")

# ---------------------------------------------------------------------------
# Normalización y WER
# ---------------------------------------------------------------------------

_UNIDADES = ["cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve",
             "diez", "once", "doce", "trece", "catorce", "quince", "dieciseis", "diecisiete",
             "dieciocho", "diecinueve", "veinte", "veintiuno", "veintidos", "veintitres",
             "veinticuatro", "veinticinco", "veintiseis", "veintisiete", "veintiocho",
             "veintinueve"]
_DECENAS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70,
            "ochenta": 80, "noventa": 90}
_CENTENAS = {"cien": 100, "ciento": 100, "doscientos": 200, "trescientos": 300,
             "cuatrocientos": 400, "quinientos": 500, "seiscientos": 600, "setecientos": 700,
             "ochocientos": 800, "novecientos": 900, "mil": 1000}
_NUMEROS = {p: i for i, p in enumerate(_UNIDADES)} | _DECENAS | _CENTENAS


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto)
                   if not unicodedata.combining(c))


def _numeros_a_cifras(palabras: list[str]) -> list[str]:
    """'trescientos cincuenta y dos' -> '352', 'dos mil' -> '2000'.

    Solo junta lo que en español forma un número; 'diecisiete seis dieciocho'
    queda '17 6 18'. Suficiente para cantidades de finca.
    """
    salida: list[str] = []
    n, i = len(palabras), 0
    while i < n:
        if palabras[i] not in _NUMEROS:
            salida.append(palabras[i])
            i += 1
            continue
        total = ultimo = _NUMEROS[palabras[i]]
        j = i + 1
        while j < n:
            q = palabras[j]
            v = _NUMEROS.get(q)
            if q == "mil" and total < 1000:
                total, ultimo = total * 1000, 1000
                j += 1
            elif v is not None and ultimo % 100 == 0 and ultimo > 100 and v < 100 \
                    or v is not None and ultimo == 1000 and v < 1000 \
                    or v is not None and ultimo == 100 and palabras[j - 1] == "ciento" and v < 100:
                total, ultimo = total + v, v
                j += 1
            elif q == "y" and ultimo in _DECENAS.values() and j + 1 < n \
                    and _NUMEROS.get(palabras[j + 1], 99) < 10:
                total, ultimo = total + _NUMEROS[palabras[j + 1]], _NUMEROS[palabras[j + 1]]
                j += 2
            else:
                break
        salida.append(str(total))
        i = j
    return salida


def normalizar(texto: str) -> list[str]:
    t = _sin_tildes(texto.lower())
    t = re.sub(r"(\d)[.,](\d{3})\b", r"\1\2", t)  # 1.200 -> 1200
    t = re.sub(r"[^a-z0-9ñ]+", " ", t)
    return _numeros_a_cifras(t.split())


def distancia(ref: list[str], hip: list[str]) -> int:
    previa = list(range(len(hip) + 1))
    for i, r in enumerate(ref, 1):
        actual = [i] + [0] * len(hip)
        for j, h in enumerate(hip, 1):
            actual[j] = min(previa[j] + 1, actual[j - 1] + 1, previa[j - 1] + (r != h))
        previa = actual
    return previa[-1]


def terminos_en(texto: str, terminos: list[str]) -> set[str]:
    norm = " " + " ".join(normalizar(texto)) + " "
    return {t for t in terminos if f" {' '.join(normalizar(t))} " in norm}


# ---------------------------------------------------------------------------
# Un modelo (en su propio proceso)
# ---------------------------------------------------------------------------


def evaluar_modelo(modelo_nombre: str, audios: list[Path]) -> dict:
    import resource

    from app.config import get_settings
    from app.transcription import whisper

    s = get_settings()
    vocab_path = Path(s.config_dir) / "vocabulario.txt"
    terminos = whisper.cargar_vocabulario(vocab_path)
    prompt = whisper.construir_prompt(terminos)

    t0 = time.perf_counter()
    modelo = whisper.get_modelo(modelo_nombre, s.whisper_device, s.whisper_compute_type,
                                s.whisper_cpu_threads)
    carga_s = time.perf_counter() - t0

    whisper.transcribir(audios[0], max_duracion_s=10_000, modelo=modelo, prompt=prompt)  # calentamiento

    filas = []
    for audio in audios:
        ref = audio.with_suffix(".txt").read_text(encoding="utf-8").strip()
        t0 = time.perf_counter()
        r = whisper.transcribir(audio, max_duracion_s=10_000, modelo=modelo, prompt=prompt)
        seg = time.perf_counter() - t0
        ref_n, hip_n = normalizar(ref), normalizar(r.texto)
        en_ref = terminos_en(ref, terminos)
        filas.append({
            "audio": audio.name, "duracion_s": r.duracion_s, "tiempo_s": round(seg, 2),
            "rtf": round(seg / r.duracion_s, 3), "errores": distancia(ref_n, hip_n),
            "palabras": len(ref_n), "confianza": r.confianza,
            "terminos_ok": len(en_ref & terminos_en(r.texto, terminos)),
            "terminos_total": len(en_ref), "texto": r.texto, "referencia": ref,
        })
    return {"modelo": modelo_nombre, "carga_s": round(carga_s, 1),
            "ram_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
            "filas": filas}


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------


def _pct(a: float, b: float) -> str:
    return f"{100 * a / b:.1f} %" if b else "-"


def imprimir(resultados: list[dict]) -> None:
    print("\n## Por audio\n")
    print("| Audio | Dur. (s) | " + " | ".join(
        f"{r['modelo']} tiempo (s) | {r['modelo']} WER | {r['modelo']} términos"
        for r in resultados) + " |")
    print("|---|---:|" + "---:|---:|---:|" * len(resultados))
    for i, fila in enumerate(resultados[0]["filas"]):
        celdas = []
        for r in resultados:
            f = r["filas"][i]
            celdas += [f"{f['tiempo_s']:.1f}", _pct(f["errores"], f["palabras"]),
                       f"{f['terminos_ok']}/{f['terminos_total']}"]
        print(f"| {fila['audio']} | {fila['duracion_s']:.1f} | " + " | ".join(celdas) + " |")

    print("\n## Resumen\n")
    print("| Modelo | Carga (s) | RAM (MB) | Tiempo total (s) | RTF | WER | Términos | Confianza media |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in resultados:
        fs = r["filas"]
        tiempo = sum(f["tiempo_s"] for f in fs)
        audio = sum(f["duracion_s"] for f in fs)
        print(f"| {r['modelo']} | {r['carga_s']} | {r['ram_mb']} | {tiempo:.1f} | "
              f"{tiempo / audio:.2f} | {_pct(sum(f['errores'] for f in fs), sum(f['palabras'] for f in fs))} | "
              f"{sum(f['terminos_ok'] for f in fs)}/{sum(f['terminos_total'] for f in fs)} | "
              f"{sum(f['confianza'] for f in fs) / len(fs):.3f} |")

    print("\n## Textos\n")
    for i, fila in enumerate(resultados[0]["filas"]):
        print(f"**{fila['audio']}**\n- referencia: {fila['referencia']}")
        for r in resultados:
            print(f"- {r['modelo']}: {r['filas'][i]['texto']}")
        print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modelos", nargs="+", default=["small", "medium"])
    ap.add_argument("--carpeta", default="tests/fixtures")
    ap.add_argument("--json", help="guarda los resultados crudos en este archivo")
    ap.add_argument("--_un_modelo", help=argparse.SUPPRESS)
    args = ap.parse_args()

    carpeta = Path(args.carpeta)
    audios = sorted(p for p in carpeta.iterdir()
                    if p.suffix.lower() in EXTENSIONES and p.with_suffix(".txt").is_file())
    if not audios:
        sys.exit(f"No hay audios con su .txt de referencia en {carpeta}")

    if args._un_modelo:
        print(json.dumps(evaluar_modelo(args._un_modelo, audios)))
        return

    print(f"{len(audios)} audios en {carpeta}; modelos: {', '.join(args.modelos)}; "
          f"hilos={os.environ.get('WHISPER_CPU_THREADS', '0')}", file=sys.stderr)
    resultados = []
    for m in args.modelos:
        print(f"... {m}", file=sys.stderr)
        salida = subprocess.run(
            [sys.executable, __file__, "--carpeta", str(carpeta), "--_un_modelo", m],
            check=True, capture_output=True, text=True,
        )
        resultados.append(json.loads(salida.stdout.strip().splitlines()[-1]))
    imprimir(resultados)
    if args.json:
        Path(args.json).write_text(json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
