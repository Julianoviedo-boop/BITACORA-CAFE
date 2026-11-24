"""Reporte de trazabilidad en PDF: plantilla Jinja2 -> HTML -> WeasyPrint (D-023).

Las fuentes van en app/reports/fuentes/ (dentro de la imagen de Docker) y se cargan
con @font-face: el PDF no depende de las fuentes instaladas en el sistema y sale
igual en todos lados.
"""

from __future__ import annotations

from pathlib import Path

import segno
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.reports.datos import DatosReporte

CARPETA = Path(__file__).resolve().parent
PLANTILLAS = CARPETA / "plantillas"

_entorno = Environment(
    loader=FileSystemLoader(PLANTILLAS),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def qr_svg(url: str) -> str:
    """El QR como data URI SVG (vectorial: se ve nítido al imprimir)."""
    return segno.make(url, error="m").svg_data_uri(scale=4, border=0, dark="#1f3d2c")


def html_reporte(datos: DatosReporte) -> str:
    return _entorno.get_template("reporte.html").render(d=datos, qr=qr_svg(datos.url_publica))


def nombre_archivo(datos: DatosReporte) -> str:
    seguro = "".join(c if c.isalnum() or c in "-_" else "-" for c in datos.lote_codigo)
    return f"reporte-{seguro or 'lote'}.pdf"


def generar_pdf(datos: DatosReporte) -> bytes:
    # Se importa aquí: WeasyPrint carga Pango al importarse y el resto de la api no
    # lo necesita.
    from weasyprint import HTML

    return HTML(string=html_reporte(datos), base_url=str(CARPETA)).write_pdf()
