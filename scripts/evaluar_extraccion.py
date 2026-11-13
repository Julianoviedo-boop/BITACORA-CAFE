"""Evalúa la extracción contra los casos de backend/tests/fixtures/casos.jsonl.

Cada línea del archivo es un caso:
    {"id": ..., "grupo": "validacion" (opcional; sin grupo = "base"),
     "texto": ..., "recibido_en": "2026-11-12T07:30:00-05:00",
     "lotes": [{"codigo", "nombre", "variedad", "finca"}],
     "esperado": [{"tipo_evento", "codigo_lote", "fecha", "hora",
                   "datos": {campo: {"valor": x, "unidad": u | [u1, u2], "opcional": bool}},
                   "faltantes": [...]}]}

- "datos" lista SOLO lo que el productor dijo. Cualquier otro dato que devuelva el
  modelo cuenta como INVENTADO (lo que más importa no hacer), salvo que el caso
  lo marque "opcional" (se acepta que esté o no).
- "unidad" puede ser una lista de unidades aceptables.
- Textos: se comparan sin mayúsculas, tildes ni signos, por raíz de palabra (5
  letras): todas las palabras esperadas deben aparecer en lo obtenido.

Se evalúa la salida YA normalizada (app/extraction/extractor.normalizar), que
es la que usaría el sistema.

Uso (llama a la API de verdad; necesita ANTHROPIC_API_KEY):
    docker compose exec api python /scripts/evaluar_extraccion.py
    python scripts/evaluar_extraccion.py --modelo claude-haiku-5-5 --effort low
    python scripts/evaluar_extraccion.py --solo lote_por_numero --salida /tmp/eval.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_RAIZ = Path(__file__).resolve().parents[1]
_BACKEND = _RAIZ / "backend"
sys.path.insert(0, str(_BACKEND if _BACKEND.is_dir() else Path.cwd()))
if _BACKEND.is_dir() and not os.environ.get("CONFIG_DIR"):
    os.environ["CONFIG_DIR"] = str(_RAIZ / "config")

# USD por millón de tokens (entrada, salida). Solo para estimar; verifique precios.
PRECIOS = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0),
           "claude-opus-5-5": (4.0, 20.0)}

# ---------------------------------------------------------------------------
# Comparación (sin API; se prueba en backend/tests/test_evaluar_extraccion.py)
# ---------------------------------------------------------------------------


def _raices(texto: Any) -> set[str]:
    t = unicodedata.normalize("NFKD", str(texto).lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    palabras = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", t)
    vacias = {"de", "la", "el", "los", "las", "y", "en", "a", "con", "del"}
    return {p[:5] for p in palabras if p not in vacias}


def texto_coincide(esperado: Any, obtenido: Any) -> bool:
    return obtenido is not None and _raices(esperado) <= _raices(obtenido)


def numero_coincide(esperado: float, obtenido: Any) -> bool:
    try:
        return abs(float(obtenido) - float(esperado)) <= 1e-6 * max(1.0, abs(float(esperado)))
    except (TypeError, ValueError):
        return False


def dato_coincide(esperado: dict, obtenido: dict | None) -> bool:
    if obtenido is None:
        return False
    if isinstance(esperado["valor"], (int, float)):
        unidades = esperado.get("unidad")
        unidades = unidades if isinstance(unidades, list) else [unidades]
        return numero_coincide(esperado["valor"], obtenido.get("valor")) and \
            obtenido.get("unidad") in unidades
    return texto_coincide(esperado["valor"], obtenido.get("valor"))


@dataclass
class Marcador:
    aciertos: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    totales: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    errores: list[str] = field(default_factory=list)
    inventados: int = 0
    eventos_esperados: int = 0
    eventos_encontrados: int = 0
    eventos_sobrantes: int = 0

    def anotar(self, clave: str, ok: bool, error: str | None = None) -> None:
        self.totales[clave] += 1
        self.aciertos[clave] += int(ok)
        if not ok and error:
            self.errores.append(error)


def _puntaje(esp: dict, obt: dict) -> int:
    p = 2 * (esp["codigo_lote"] == obt.get("codigo_lote"))
    p += esp["fecha"] == obt.get("fecha")
    p += sum(dato_coincide(d, (obt.get("datos") or {}).get(c)) for c, d in esp["datos"].items())
    return p


def emparejar(esperados: list[dict], obtenidos: list[dict]) -> tuple[list[tuple[dict, dict]],
                                                                      list[dict], list[dict]]:
    """Empareja por tipo de evento; entre varios del mismo tipo, el que más se parece."""
    libres = list(range(len(obtenidos)))
    pares, perdidos = [], []
    for esp in esperados:
        candidatos = [i for i in libres if obtenidos[i]["tipo_evento"] == esp["tipo_evento"]]
        if not candidatos:
            perdidos.append(esp)
            continue
        mejor = max(candidatos, key=lambda i: _puntaje(esp, obtenidos[i]))
        libres.remove(mejor)
        pares.append((esp, obtenidos[mejor]))
    return pares, perdidos, [obtenidos[i] for i in libres]


def calificar_caso(caso_id: str, esperados: list[dict], obtenidos: list[dict],
                   m: Marcador) -> None:
    pares, perdidos, sobrantes = emparejar(esperados, obtenidos)
    m.eventos_esperados += len(esperados)
    m.eventos_encontrados += len(pares)
    m.eventos_sobrantes += len(sobrantes)
    m.anotar("caso_completo_eventos", not perdidos and not sobrantes)
    for p in perdidos:
        m.errores.append(f"[{caso_id}] NO DETECTÓ {p['tipo_evento']} (lote {p['codigo_lote']})")
    for s in sobrantes:
        m.errores.append(f"[{caso_id}] EVENTO DE MÁS: {s['tipo_evento']} lote "
                         f"{s.get('codigo_lote')} «{s.get('fragmento', '')}»")

    for esp, obt in pares:
        pre = f"[{caso_id}] {esp['tipo_evento']}:"
        for clave in ("codigo_lote", "fecha", "hora"):
            m.anotar(clave, esp[clave] == obt.get(clave),
                     f"{pre} {clave} esperado {esp[clave]!r}, obtuvo {obt.get(clave)!r}")
        datos_obt = obt.get("datos") or {}
        for campo, d in esp["datos"].items():
            if d.get("opcional"):
                continue
            m.anotar(f"valores.{campo}", dato_coincide(d, datos_obt.get(campo)),
                     f"{pre} {campo} esperado {d['valor']!r} {d.get('unidad') or ''}".rstrip()
                     + f", obtuvo {datos_obt.get(campo)!r}")
        for campo, valor in datos_obt.items():
            if valor is not None and campo not in esp["datos"]:
                m.inventados += 1
                m.errores.append(f"{pre} INVENTÓ {campo}={valor!r}")
        m.anotar("faltantes", sorted(esp["faltantes"]) == sorted(obt.get("faltantes") or []),
                 f"{pre} faltantes esperado {sorted(esp['faltantes'])}, "
                 f"obtuvo {sorted(obt.get('faltantes') or [])}")


def resumen(m: Marcador) -> str:
    lineas = []
    rec = m.eventos_encontrados / m.eventos_esperados if m.eventos_esperados else 1.0
    tot_obt = m.eventos_encontrados + m.eventos_sobrantes
    prec = m.eventos_encontrados / tot_obt if tot_obt else 1.0
    lineas.append(f"Eventos: {m.eventos_encontrados}/{m.eventos_esperados} detectados "
                  f"(recall {rec:.0%}), {m.eventos_sobrantes} de más (precisión {prec:.0%})")
    lineas.append(f"Datos inventados: {m.inventados}")
    lineas.append("")
    lineas.append(f"{'Campo':32} {'Aciertos':>10} {'%':>6}")
    orden = ["caso_completo_eventos", "codigo_lote", "fecha", "hora", "faltantes"]
    claves = orden + sorted(k for k in m.totales if k not in orden)
    total_v = sum(m.totales[k] for k in m.totales if k.startswith("valores."))
    ok_v = sum(m.aciertos[k] for k in m.totales if k.startswith("valores."))
    for k in claves:
        if k in m.totales:
            lineas.append(f"{k:32} {m.aciertos[k]:>4}/{m.totales[k]:<5} "
                          f"{m.aciertos[k] / m.totales[k]:>6.0%}")
    if total_v:
        lineas.append(f"{'valores (todos)':32} {ok_v:>4}/{total_v:<5} {ok_v / total_v:>6.0%}")
    return "\n".join(lineas)


# ---------------------------------------------------------------------------
# Ejecución
# ---------------------------------------------------------------------------


def cargar_casos(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _correr_caso(caso: dict, cliente: Any) -> tuple[dict, list[dict] | None, dict | None, str | None]:
    from app.extraction.extractor import extraer
    from app.extraction.prompts import LoteContexto

    try:
        r = extraer(caso["texto"], recibido_en=dt.datetime.fromisoformat(caso["recibido_en"]),
                    lotes=[LoteContexto(**l) for l in caso["lotes"]], cliente=cliente)
    except Exception as e:  # se reporta como caso fallido, no se detiene la evaluación
        return caso, None, None, f"{type(e).__name__}: {e}"
    return caso, [e.model_dump(mode="json") for e in r.eventos], r.uso, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--casos", type=Path,
                        default=_BACKEND / "tests" / "fixtures" / "casos.jsonl"
                        if _BACKEND.is_dir() else Path("tests/fixtures/casos.jsonl"))
    parser.add_argument("--modelo", help="Por defecto ANTHROPIC_MODEL")
    parser.add_argument("--effort", help="Por defecto ANTHROPIC_EFFORT")
    parser.add_argument("--solo", nargs="*", help="Ids de casos a correr")
    parser.add_argument("--grupo", help="Solo los casos de ese grupo (sin grupo = base)")
    parser.add_argument("--paralelo", type=int, default=4)
    parser.add_argument("--salida", type=Path, help="Guarda lo obtenido en un JSON")
    args = parser.parse_args()

    from app.extraction.llm import ClienteLLM

    casos = cargar_casos(args.casos)
    if args.solo:
        casos = [c for c in casos if c["id"] in set(args.solo)]
    if args.grupo:
        casos = [c for c in casos if c.get("grupo", "base") == args.grupo]
    cliente = ClienteLLM(modelo=args.modelo, effort=args.effort)
    if not casos:
        print("No hay casos con ese filtro")
        return 1
    print(f"Modelo {cliente.modelo}, esfuerzo {cliente.effort}, {len(casos)} casos\n")

    with ThreadPoolExecutor(max_workers=max(1, args.paralelo)) as pool:
        resultados = list(pool.map(lambda c: _correr_caso(c, cliente), casos))

    m = Marcador()
    tokens = defaultdict(int)
    fallidos = []
    guardado = []
    for caso, obtenidos, uso, error in resultados:
        guardado.append({"id": caso["id"], "obtenido": obtenidos, "uso": uso, "error": error})
        if error:
            fallidos.append(f"[{caso['id']}] FALLÓ LA LLAMADA: {error}")
            obtenidos = []
        else:
            for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                      "cache_read_input_tokens"):
                tokens[k] += uso[k]
        calificar_caso(caso["id"], caso["esperado"], obtenidos, m)

    print(resumen(m))
    print(f"\nTokens: entrada {tokens['input_tokens']}, salida {tokens['output_tokens']}, "
          f"caché escrita {tokens['cache_creation_input_tokens']}, "
          f"caché leída {tokens['cache_read_input_tokens']}")
    if cliente.modelo in PRECIOS:
        pin, pout = PRECIOS[cliente.modelo]
        costo = (tokens["input_tokens"] * pin + tokens["output_tokens"] * pout
                 + tokens["cache_creation_input_tokens"] * pin * 1.25
                 + tokens["cache_read_input_tokens"] * pin * 0.1) / 1e6
        print(f"Costo estimado: US$ {costo:.4f}")
    errores = fallidos + m.errores
    print(f"\nErrores ({len(errores)}):")
    for e in errores:
        print("  " + e)
    if args.salida:
        args.salida.write_text(json.dumps(guardado, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if fallidos else 0


if __name__ == "__main__":
    raise SystemExit(main())
