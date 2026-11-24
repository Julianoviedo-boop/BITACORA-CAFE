"""Registra una organización de prueba con un número receptor real de WhatsApp.

El seed (scripts/seed.py) usa números ficticios. Para probar con WhatsApp de verdad
la organización debe tener como `whatsapp_address` el phone_number_id de Meta
(lo que OpenBSP manda como `organization_address`), y quien escribe debe estar
registrado como productor de esa organización.

Uso (con la base ya migrada):
    docker compose exec api python /scripts/registrar_organizacion.py \\
        --numero 1519752507881349 --telefono +573001234567 --productor "Su nombre"

Crea, si no existen: la organización, una finca "Finca de prueba", un lote PRU-01
y el productor con ese teléfono. Es idempotente: si ya existe una organización
con ese número la reutiliza, y si el productor ya existe no lo duplica.
"""

import argparse
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

# Permite correrlo desde el contenedor (/scripts, con el backend en /app) o
# desde la raíz del repositorio (backend/ al lado de scripts/).
_repo_backend = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(_repo_backend if _repo_backend.is_dir() else Path.cwd()))

from app.channels.openbsp import to_e164  # noqa: E402
from app.db.repositories import Repositories, unit_of_work  # noqa: E402
from app.db.repositories.routing import find_organization_id_by_whatsapp_address  # noqa: E402

# Espacio distinto al del seed: así un número nunca choca con las organizaciones de ejemplo.
NAMESPACE = uuid.UUID("0b8e4d2a-3c5f-4e71-9d26-8a1f7c6e5b42")


@dataclass
class Resultado:
    organization_id: uuid.UUID
    organizacion_creada: bool
    productor_creado: bool
    productor_phone: str | None


def registrar(
    repos: Repositories,
    numero: str,
    *,
    nombre: str = "Organización de prueba",
    telefono: str | None = None,
    productor: str = "Productor de prueba",
) -> Resultado:
    numero = numero.strip()
    if not numero.isdigit():
        raise ValueError(f"El número receptor debe ser solo dígitos (phone_number_id): {numero!r}")
    phone = None
    if telefono is not None:
        phone = to_e164(telefono)
        if phone is None:
            raise ValueError(f"Teléfono inválido: {telefono!r}. Use formato +573001234567")

    org_id = find_organization_id_by_whatsapp_address(repos.session, numero)
    creada = org_id is None
    if creada:
        org_id = uuid.uuid5(NAMESPACE, numero)
        repos.organizations.create(
            org_id, name=nombre, slug=f"prueba-{numero}", whatsapp_address=numero
        )

    farms = repos.farms.list(org_id)
    if farms:
        farm = farms[0]
    else:
        farm = repos.farms.add(org_id, name="Finca de prueba")
    if repos.lots.get_by_code(org_id, "PRU-01") is None:
        repos.lots.add(org_id, farm_id=farm.id, code="PRU-01", name="Lote de prueba")

    productor_creado = False
    if phone is not None and repos.producers.get_by_phone(org_id, phone) is None:
        repos.producers.add(org_id, farm_id=farm.id, full_name=productor, phone=phone)
        productor_creado = True

    return Resultado(org_id, creada, productor_creado, phone)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--numero", required=True,
                        help="phone_number_id de Meta (el que sale en el log)")
    parser.add_argument("--nombre", default="Organización de prueba",
                        help="Nombre de la organización")
    parser.add_argument("--telefono",
                        help="Su celular, que escribe al bot (p. ej. +573001234567)")
    parser.add_argument("--productor", default="Productor de prueba",
                        help="Nombre del productor")
    args = parser.parse_args()

    try:
        with unit_of_work() as repos:
            r = registrar(repos, args.numero, nombre=args.nombre,
                          telefono=args.telefono, productor=args.productor)
    except ValueError as exc:
        sys.exit(f"Error: {exc}")

    print(f"Organización {r.organization_id}: "
          f"{'creada' if r.organizacion_creada else 'ya existía'} (número {args.numero})")
    if r.productor_phone:
        print(f"Productor {r.productor_phone}: "
              f"{'creado' if r.productor_creado else 'ya existía'}")
    else:
        print("Sin --telefono: no se registró productor; sus mensajes se ignorarán.")


if __name__ == "__main__":
    main()
