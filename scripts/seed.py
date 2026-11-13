"""Carga datos de ejemplo: 2 organizaciones con sus fincas, productores y lotes.

Uso (con la base ya migrada):
    docker compose exec api alembic upgrade head
    docker compose exec api python /scripts/seed.py

Es idempotente: los ids de las organizaciones se derivan de su slug, así que si
una organización ya existe se salta. Todo pasa por la capa de repositorios.
"""

import sys
import uuid
from pathlib import Path

# Permite correrlo desde el contenedor (/scripts, con el backend en /app) o
# desde la raíz del repositorio (backend/ al lado de scripts/).
_repo_backend = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(_repo_backend if _repo_backend.is_dir() else Path.cwd()))

from app.db.repositories import Repositories, unit_of_work  # noqa: E402

SEED_NAMESPACE = uuid.UUID("6f1c2a4e-7b1d-4c39-9a55-2f0e8d7c3b10")

DATOS = [
    {
        "slug": "asocafe-huila",
        "name": "Asociación de Caficultores del Sur del Huila",
        # Número receptor ficticio (en OpenBSP sería el phone_number_id real).
        "whatsapp_address": "100000000000001",
        "fincas": [
            {
                "name": "El Mirador",
                "municipality": "Pitalito",
                "department": "Huila",
                "altitude_m": 1650,
                "productores": [
                    {"full_name": "Rosa Elena Muñoz", "phone": "+573101234501"},
                ],
                "lotes": [
                    {"code": "MIR-01", "name": "La Esperanza", "variety": "Castillo",
                     "process": "lavado"},
                    {"code": "MIR-02", "name": "El Guamo", "variety": "Caturra",
                     "process": "honey"},
                ],
            },
            {
                "name": "Buenavista",
                "municipality": "Acevedo",
                "department": "Huila",
                "altitude_m": 1780,
                "productores": [
                    {"full_name": "Jairo Andrés Cerquera", "phone": "+573101234502"},
                ],
                "lotes": [
                    {"code": "BUE-01", "name": "La Loma", "variety": "Geisha",
                     "process": "natural"},
                ],
            },
        ],
    },
    {
        "slug": "cafe-sierra-nevada",
        "name": "Cooperativa Café Sierra Nevada",
        "whatsapp_address": "100000000000002",
        "fincas": [
            {
                "name": "La Victoria",
                "municipality": "Santa Marta",
                "department": "Magdalena",
                "altitude_m": 1200,
                "productores": [
                    {"full_name": "Luz Dary Arias", "phone": "+573201234503"},
                    # Mismo teléfono que en la otra organización: debe poder existir.
                    {"full_name": "Rosa Elena Muñoz", "phone": "+573101234501"},
                ],
                "lotes": [
                    # Mismo código que en la otra organización: los códigos son por organización.
                    {"code": "MIR-01", "name": "El Cedro", "variety": "Típica",
                     "process": "lavado"},
                    {"code": "VIC-02", "name": "Los Naranjos", "variety": "Colombia",
                     "process": "lavado"},
                ],
            },
        ],
    },
]


def seed_organization(repos: Repositories, data: dict) -> tuple[uuid.UUID, bool]:
    org_id = uuid.uuid5(SEED_NAMESPACE, data["slug"])
    existing = repos.organizations.get(org_id)
    if existing is not None:
        # Bases sembradas antes de que existiera el número receptor.
        if existing.whatsapp_address is None:
            repos.organizations.update(org_id, whatsapp_address=data["whatsapp_address"])
        return org_id, False
    repos.organizations.create(
        org_id, name=data["name"], slug=data["slug"], whatsapp_address=data["whatsapp_address"]
    )
    for finca in data["fincas"]:
        farm = repos.farms.add(
            org_id,
            name=finca["name"],
            municipality=finca["municipality"],
            department=finca["department"],
            altitude_m=finca["altitude_m"],
        )
        for p in finca["productores"]:
            repos.producers.add(org_id, farm_id=farm.id, **p)
        for lote in finca["lotes"]:
            repos.lots.add(org_id, farm_id=farm.id, **lote)
    return org_id, True


def main() -> None:
    with unit_of_work() as repos:
        for data in DATOS:
            org_id, created = seed_organization(repos, data)
            estado = "creada" if created else "ya existía, se salta"
            print(f"{data['name']} ({org_id}): {estado}")
            for lot in repos.lots.list(org_id):
                print(f"  - {lot.code} {lot.name} (token público: {lot.public_token[:8]}…)")


if __name__ == "__main__":
    main()
