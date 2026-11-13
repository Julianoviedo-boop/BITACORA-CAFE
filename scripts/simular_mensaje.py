"""Envía mensajes falsos de WhatsApp al webhook, con el mismo formato que OpenBSP.

Sirve para probar el flujo completo sin WhatsApp. Con WHATSAPP_CHANNEL=fake las
respuestas no se envían: aparecen en el log del worker (docker compose logs -f worker).

Uso (dentro del contenedor, que ya tiene las variables del .env):
    docker compose exec api python /scripts/simular_mensaje.py --telefono +573101234501 --texto "Hoy abonamos MIR-01"
    docker compose exec api python /scripts/simular_mensaje.py --telefono +573101234501 --texto "SÍ"
    docker compose exec api python /scripts/simular_mensaje.py --telefono +573101234501 --audio nota.ogg
    docker compose exec api python /scripts/simular_mensaje.py --telefono +573101234501 --imagen foto.jpg --texto "Así quedó"

Casos útiles:
    --external-id wamid.prueba-1   repetirlo dos veces prueba el duplicado
    --propio                       mensaje propio (sender_address null): se ignora
    --numero 999                   número receptor que no es de ninguna organización

--audio e --imagen mandan la referencia fixture://<archivo>; el canal falso la lee
de FAKE_MEDIA_DIR (por defecto backend/tests/fixtures).

Solo usa la librería estándar: también corre desde la raíz del repositorio
(lee el .env si las variables no están definidas).
"""

import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

# Número receptor de la organización de ejemplo asocafe-huila (scripts/seed.py).
NUMERO_POR_DEFECTO = "100000000000001"

# Tipos de audio de WhatsApp que Python no siempre conoce (depende de /etc/mime.types).
for _tipo, _ext in (("audio/ogg", ".ogg"), ("audio/ogg", ".opus"), ("audio/mp4", ".m4a"),
                    ("audio/amr", ".amr")):
    mimetypes.add_type(_tipo, _ext)


def _leer_env() -> dict[str, str]:
    """Variables del .env de la raíz, para cuando el script corre fuera de Docker."""
    path = Path(__file__).resolve().parents[1] / ".env"
    valores: dict[str, str] = {}
    if path.is_file():
        for linea in path.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                clave, valor = linea.split("=", 1)
                valores[clave.strip()] = valor.strip().strip('"').strip("'")
    return valores


def _var(nombre: str, env_archivo: dict[str, str]) -> str:
    return os.environ.get(nombre) or env_archivo.get(nombre, "")


def construir_payload(
    *,
    telefono: str,
    numero: str,
    texto: str | None = None,
    audio: str | None = None,
    imagen: str | None = None,
    external_id: str | None = None,
    propio: bool = False,
) -> dict:
    """Arma el cuerpo {entity, action, data} que OpenBSP manda para un mensaje nuevo."""
    direccion = telefono.removeprefix("+")
    archivo, tipo = (audio, "audio") if audio else (imagen, "image") if imagen else (None, None)
    if archivo:
        content = {
            "version": "1",
            "type": "file",
            "kind": tipo,
            "file": {
                "mime_type": mimetypes.guess_type(archivo)[0] or "application/octet-stream",
                "uri": f"fixture://{archivo}",
                "name": archivo,
            },
        }
        if texto:
            content["text"] = texto
    else:
        content = {"version": "1", "type": "text", "kind": "text", "text": texto or ""}
    return {
        "entity": "messages",
        "action": "insert",
        "data": {
            "id": str(uuid.uuid4()),
            "external_id": external_id or f"wamid.simulado.{uuid.uuid4().hex}",
            "service": "whatsapp",
            "organization_address": numero,
            "conversation_address": direccion,
            "sender_address": None if propio else direccion,
            "content": content,
            "status": {"pending": datetime.now(UTC).isoformat()},
            "timestamp": datetime.now(UTC).isoformat(),
        },
    }


def enviar(url: str, payload: dict, token: str = "") -> tuple[int, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simula un mensaje de WhatsApp vía OpenBSP")
    parser.add_argument("--telefono", required=True, help="Teléfono del productor, ej. +573101234501")
    parser.add_argument("--numero", default=NUMERO_POR_DEFECTO, help="Número receptor (phone_number_id)")
    parser.add_argument("--texto", help="Texto del mensaje (o pie de foto con --imagen)")
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument("--audio", help="Archivo de audio en FAKE_MEDIA_DIR, ej. nota.ogg")
    grupo.add_argument("--imagen", help="Archivo de imagen en FAKE_MEDIA_DIR, ej. foto.jpg")
    parser.add_argument("--external-id", help="Id del mensaje; repítalo para probar duplicados")
    parser.add_argument("--propio", action="store_true", help="Mensaje propio (sender_address null)")
    parser.add_argument("--api", default=None, help="URL de la API (por defecto http://localhost:8000)")
    args = parser.parse_args(argv)

    if not (args.texto or args.audio or args.imagen):
        parser.error("Indique --texto, --audio o --imagen")

    env_archivo = _leer_env()
    secreto = _var("OPENBSP_WEBHOOK_SECRET", env_archivo)
    if not secreto:
        print("Falta OPENBSP_WEBHOOK_SECRET (en el entorno o en el .env)", file=sys.stderr)
        return 2
    api = (args.api or _var("API_URL", env_archivo) or "http://localhost:8000").rstrip("/")

    payload = construir_payload(
        telefono=args.telefono, numero=args.numero, texto=args.texto, audio=args.audio,
        imagen=args.imagen, external_id=args.external_id, propio=args.propio,
    )
    status, cuerpo = enviar(
        f"{api}/webhooks/openbsp/{secreto}", payload, _var("OPENBSP_WEBHOOK_TOKEN", env_archivo)
    )
    print(f"external_id: {payload['data']['external_id']}")
    print(f"HTTP {status}: {cuerpo}")
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
