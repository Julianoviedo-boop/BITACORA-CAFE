"""PRUEBA OBLIGATORIA: la organización A no puede leer ni modificar nada de la
organización B por ningún método del repositorio.

Cómo funciona:
1. Se crean dos organizaciones con un grafo completo cada una (finca, productor,
   lote, mensaje, evento, alerta, reporte y conversación).
2. Para cada método público de cada repositorio hay uno o más "ataques": se llama
   con organization_id = A apuntando a datos de B.
3. Después de cada llamada se verifica que:
   - el resultado no contiene ninguna fila de B, y
   - todas las filas de B en la base siguen exactamente iguales.
4. `test_todo_metodo_publico_tiene_ataque` falla si alguien agrega un método nuevo
   a un repositorio sin agregar aquí su caso de aislamiento.
"""

import inspect
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import BUSINESS_MODELS, Organization
from app.db.repositories import OrganizationRequiredError, ProtectedFieldError, Repositories

pytestmark = pytest.mark.db


# ---------------------------------------------------------------------------
# Datos: dos organizaciones completas
# ---------------------------------------------------------------------------


@dataclass
class OrgData:
    org_id: uuid.UUID
    farm_id: uuid.UUID
    producer_id: uuid.UUID
    phone: str
    lot_id: uuid.UUID
    lot_code: str
    public_token: str
    message_id: uuid.UUID
    external_id: str
    event_id: uuid.UUID
    alert_id: uuid.UUID
    report_id: uuid.UUID
    conversation_id: uuid.UUID


@dataclass
class World:
    a: OrgData
    b: OrgData


def _build_org(repos: Repositories, slug: str, phone: str, lot_code: str) -> OrgData:
    org_id = uuid.uuid4()
    repos.organizations.create(org_id, name=f"Org {slug}", slug=slug)
    farm = repos.farms.add(org_id, name=f"Finca {slug}", municipality="Pitalito")
    producer = repos.producers.add(org_id, full_name="Productor", phone=phone, farm_id=farm.id)
    lot = repos.lots.add(org_id, farm_id=farm.id, code=lot_code, name="La Esperanza")
    msg, created = repos.messages.add_if_new(
        org_id, f"wamid.{slug}", from_phone=phone, message_type="texto",
        text_body="Hoy abonamos", producer_id=producer.id,
    )
    assert created and msg is not None
    event = repos.events.add(
        org_id, lot_id=lot.id, message_id=msg.id, event_type="abonada",
        occurred_at=datetime(2026, 11, 1, tzinfo=UTC), data={"insumo": "17-6-18"},
    )
    alert = repos.alerts.add(
        org_id, lot_id=lot.id, event_id=event.id, rule_code="carencia", message="Espere"
    )
    report = repos.reports.add(
        org_id, lot_id=lot.id, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31)
    )
    conversation = repos.conversations.get_or_create(org_id, producer.id)
    return OrgData(
        org_id=org_id, farm_id=farm.id, producer_id=producer.id, phone=phone,
        lot_id=lot.id, lot_code=lot.code, public_token=lot.public_token,
        message_id=msg.id, external_id=msg.external_id, event_id=event.id,
        alert_id=alert.id, report_id=report.id, conversation_id=conversation.id,
    )


@pytest.fixture
def repos(db_session: Session) -> Repositories:
    return Repositories(db_session)


@pytest.fixture
def world(repos: Repositories) -> World:
    # Mismo teléfono y mismo código de lote en ambas: deben poder coexistir y
    # aun así no confundirse.
    return World(
        a=_build_org(repos, "org-a", "+573001112233", "ESP-01"),
        b=_build_org(repos, "org-b", "+573001112233", "ESP-01"),
    )


# ---------------------------------------------------------------------------
# Ataques: (repositorio, método, descripción, constructor de argumentos, esperado)
#   esperado = "sin_fuga"   -> la llamada termina pero no devuelve ni toca nada de B
#   esperado = "integridad" -> PostgreSQL la rechaza (FK compuesta entre organizaciones)
# ---------------------------------------------------------------------------

Args = tuple[tuple[Any, ...], dict[str, Any]]


@dataclass(frozen=True)
class Attack:
    repo: str
    method: str
    desc: str
    build: Callable[[World], Args]
    expect: str = "sin_fuga"

    @property
    def id(self) -> str:
        return f"{self.repo}.{self.method}:{self.desc}"


def _a(*args: Any, **kwargs: Any) -> Args:
    return args, kwargs


def _generic(repo: str, ref: str, update_field: dict[str, Any]) -> list[Attack]:
    """Ataques comunes a todos los repositorios con get/list/update/delete."""
    return [
        Attack(repo, "get", "id de B", lambda w: _a(getattr(w.b, ref))),
        Attack(repo, "list", "lista de A", lambda w: _a()),
        Attack(repo, "list", "paginando lejos", lambda w: _a(limit=10_000, offset=0)),
        Attack(repo, "update", "fila de B", lambda w: _a(getattr(w.b, ref), **update_field)),
        Attack(repo, "delete", "fila de B", lambda w: _a(getattr(w.b, ref))),
    ]


NOW = datetime(2026, 11, 2, tzinfo=UTC)

ATTACKS: list[Attack] = [
    # organizations
    Attack("organizations", "get", "propia", lambda w: _a()),
    Attack("organizations", "update", "propia", lambda w: _a(name="Hackeada")),
    # Crear con el id de B no la sobrescribe: choca con la llave primaria.
    Attack("organizations", "create", "con id de B",
           lambda w: _a(name="Falsa", slug="falsa"), "integridad"),
    # farms
    *_generic("farms", "farm_id", {"name": "Hackeada"}),
    Attack("farms", "add", "finca nueva en A", lambda w: _a(name="Nueva")),
    # producers
    *_generic("producers", "producer_id", {"full_name": "Hackeado"}),
    Attack("producers", "add", "colgado de finca de B",
           lambda w: _a(full_name="X", phone="+573009999999", farm_id=w.b.farm_id), "integridad"),
    Attack("producers", "get_by_phone", "teléfono de B", lambda w: _a(w.b.phone)),
    Attack("producers", "record_consent", "productor de B",
           lambda w: _a(w.b.producer_id, "revocado")),
    Attack("producers", "list_by_farm", "finca de B", lambda w: _a(w.b.farm_id)),
    Attack("producers", "list_by_farm", "finca de B con consentimiento",
           lambda w: _a(w.b.farm_id, consent_status="pendiente")),
    # lots
    *_generic("lots", "lot_id", {"name": "Hackeado"}),
    Attack("lots", "update", "mover lote de A a finca de B",
           lambda w: _a(w.a.lot_id, farm_id=w.b.farm_id), "integridad"),
    Attack("lots", "add", "en finca de B",
           lambda w: _a(farm_id=w.b.farm_id, name="X", code="NUEVO"), "integridad"),
    Attack("lots", "get_by_code", "código de B", lambda w: _a("ESP-01")),
    Attack("lots", "get_by_public_token", "token de B", lambda w: _a(w.b.public_token)),
    Attack("lots", "list_by_farm", "finca de B", lambda w: _a(w.b.farm_id)),
    Attack("lots", "list_active", "lotes activos de A", lambda w: _a()),
    Attack("lots", "list_all", "todos los lotes de A", lambda w: _a()),
    Attack("lots", "regenerate_public_token", "lote de B", lambda w: _a(w.b.lot_id)),
    # inbound_messages
    *_generic("messages", "message_id", {"transcription": "Hackeada"}),
    Attack("messages", "add", "con productor de B",
           lambda w: _a(external_id="wamid.nuevo", from_phone="+57300", message_type="texto",
                        producer_id=w.b.producer_id), "integridad"),
    Attack("messages", "get_by_external_id", "external_id de B", lambda w: _a(w.b.external_id)),
    Attack("messages", "add_if_new", "external_id de B",
           lambda w: _a(w.b.external_id, from_phone="+57300", message_type="texto")),
    Attack("messages", "set_processing_status", "mensaje de B",
           lambda w: _a(w.b.message_id, "error", error_detail="hack")),
    # lot_events
    *_generic("events", "event_id", {"data": {"hack": True}}),
    Attack("events", "add", "en lote de B",
           lambda w: _a(lot_id=w.b.lot_id, event_type="abonada", occurred_at=NOW), "integridad"),
    Attack("events", "add", "con mensaje de B",
           lambda w: _a(lot_id=w.a.lot_id, message_id=w.b.message_id, event_type="abonada",
                        occurred_at=NOW), "integridad"),
    Attack("events", "list_by_lot", "lote de B", lambda w: _a(w.b.lot_id)),
    Attack("events", "list_by_lot", "lote de B pendientes",
           lambda w: _a(w.b.lot_id, status="pendiente")),
    Attack("events", "summary_by_lot", "resumen de A", lambda w: _a(status="pendiente")),
    Attack("events", "confirm", "evento de B", lambda w: _a(w.b.event_id)),
    Attack("events", "reject", "evento de B", lambda w: _a(w.b.event_id)),
    # alerts
    *_generic("alerts", "alert_id", {"message": "Hackeada"}),
    Attack("alerts", "add", "en lote de B",
           lambda w: _a(lot_id=w.b.lot_id, rule_code="x", message="x"), "integridad"),
    Attack("alerts", "add", "con evento de B",
           lambda w: _a(lot_id=w.a.lot_id, event_id=w.b.event_id, rule_code="x", message="x"),
           "integridad"),
    Attack("alerts", "list_pending", "pendientes de A", lambda w: _a()),
    Attack("alerts", "mark_sent", "alerta de B", lambda w: _a(w.b.alert_id)),
    Attack("alerts", "open_if_absent", "en lote de B",
           lambda w: _a(lot_id=w.b.lot_id, rule_code="x", message="x"), "integridad"),
    Attack("alerts", "open_if_absent", "con evento de B",
           lambda w: _a(lot_id=w.a.lot_id, event_id=w.b.event_id, rule_code="x", message="x"),
           "integridad"),
    # B tiene abierta la misma regla en un lote con el mismo código: A sí puede abrirla.
    Attack("alerts", "open_if_absent", "regla abierta en B",
           lambda w: _a(lot_id=w.a.lot_id, rule_code="otra", message="x")),
    Attack("alerts", "list_open", "abiertas de A", lambda w: _a()),
    Attack("alerts", "list_open", "lote de B", lambda w: _a(lot_id=w.b.lot_id)),
    Attack("alerts", "resolve_open", "alerta de B", lambda w: _a(w.b.lot_id, "carencia")),
    # reports
    *_generic("reports", "report_id", {"file_ref": "hack.pdf"}),
    Attack("reports", "add", "en lote de B",
           lambda w: _a(lot_id=w.b.lot_id, date_from=date(2026, 1, 1), date_to=date(2026, 2, 1)),
           "integridad"),
    Attack("reports", "list_by_lot", "lote de B", lambda w: _a(w.b.lot_id)),
    # conversations
    *_generic("conversations", "conversation_id", {"last_prompt": "Hackeada"}),
    Attack("conversations", "add", "con productor de B",
           lambda w: _a(producer_id=w.b.producer_id), "integridad"),
    Attack("conversations", "update", "pasarla a un mensaje de B",
           lambda w: _a(w.a.conversation_id, source_message_id=w.b.message_id), "integridad"),
    Attack("conversations", "get_by_producer", "productor de B",
           lambda w: _a(w.b.producer_id)),
    Attack("conversations", "get_by_producer", "productor de B con bloqueo",
           lambda w: _a(w.b.producer_id, for_update=True)),
    Attack("conversations", "get_or_create", "productor de B",
           lambda w: _a(w.b.producer_id), "integridad"),
]


# ---------------------------------------------------------------------------
# Utilidades de verificación
# ---------------------------------------------------------------------------


def _snapshot(session: Session, org_id: uuid.UUID) -> dict[str, list[dict[str, Any]]]:
    """Estado real en la base de todo lo que pertenece a una organización."""
    session.flush()
    snap: dict[str, list[dict[str, Any]]] = {}
    org_table = Organization.__table__
    snap["organizations"] = [
        dict(r) for r in session.execute(select(org_table).where(org_table.c.id == org_id)).mappings()
    ]
    for model in BUSINESS_MODELS:
        table = model.__table__
        rows = session.execute(
            select(table).where(table.c.organization_id == org_id).order_by(table.c.id)
        ).mappings()
        snap[table.name] = [dict(r) for r in rows]
    return snap


def _flatten(result: Any) -> list[Any]:
    if result is None or isinstance(result, bool | str):
        return []
    if isinstance(result, Base):
        return [result]
    if isinstance(result, tuple | list):
        return [x for item in result for x in _flatten(item)]
    if hasattr(result, "__iter__"):
        return [x for item in result for x in _flatten(item)]
    return []


def _owner(obj: Base) -> uuid.UUID:
    return obj.id if isinstance(obj, Organization) else obj.organization_id


def _org_arg(attack: Attack, world: World, org_id: Any) -> Any:
    # organizations.create recibe el id de la organización a crear: el ataque
    # es intentar crearla con el id de B.
    if attack.repo == "organizations" and attack.method == "create":
        return world.b.org_id
    return org_id


# ---------------------------------------------------------------------------
# Pruebas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("attack", ATTACKS, ids=lambda a: a.id)
def test_a_no_puede_leer_ni_modificar_b(
    attack: Attack, world: World, repos: Repositories, db_session: Session
) -> None:
    before_b = _snapshot(db_session, world.b.org_id)
    assert all(before_b.values()), "B debe tener al menos una fila en cada tabla"

    method = getattr(getattr(repos, attack.repo), attack.method)
    args, kwargs = attack.build(world)
    org_arg = _org_arg(attack, world, world.a.org_id)

    if attack.expect == "integridad":
        with pytest.raises(IntegrityError):
            with db_session.begin_nested():
                method(org_arg, *args, **kwargs)
        result = None
    else:
        with db_session.begin_nested():
            result = method(org_arg, *args, **kwargs)

    leaked = [o for o in _flatten(result) if _owner(o) != world.a.org_id]
    assert not leaked, f"{attack.id} devolvió filas de otra organización: {leaked}"
    assert _snapshot(db_session, world.b.org_id) == before_b, f"{attack.id} modificó datos de B"


def _public_methods() -> set[tuple[str, str]]:
    probe = Repositories(None)  # type: ignore[arg-type] - solo se inspeccionan las clases
    found = set()
    for attr, repo in vars(probe).items():
        if attr == "session":
            continue
        for name, fn in inspect.getmembers(type(repo), inspect.isfunction):
            if not name.startswith("_"):
                found.add((attr, name))
    return found


def test_todo_metodo_publico_tiene_ataque() -> None:
    covered = {(a.repo, a.method) for a in ATTACKS}
    missing = _public_methods() - covered
    assert not missing, (
        "Métodos de repositorio sin prueba de aislamiento entre organizaciones: "
        + ", ".join(f"{r}.{m}" for r, m in sorted(missing))
    )


def test_todo_metodo_publico_recibe_organization_id_primero() -> None:
    probe = Repositories(None)  # type: ignore[arg-type] - solo se inspeccionan las clases
    for repo, name in sorted(_public_methods()):
        fn = getattr(type(getattr(probe, repo)), name)
        params = list(inspect.signature(fn).parameters)
        assert params[:2] == ["self", "organization_id"], f"{repo}.{name}: {params}"


@pytest.mark.parametrize("bad_org", [None, "org-a", 123], ids=["None", "texto", "entero"])
@pytest.mark.parametrize("attack", ATTACKS, ids=lambda a: a.id)
def test_sin_organization_id_valido_falla(
    attack: Attack, bad_org: Any, world: World, repos: Repositories, db_session: Session
) -> None:
    method = getattr(getattr(repos, attack.repo), attack.method)
    args, kwargs = attack.build(world)
    before_b = _snapshot(db_session, world.b.org_id)
    with pytest.raises(OrganizationRequiredError):
        with db_session.begin_nested():
            method(bad_org, *args, **kwargs)
    assert _snapshot(db_session, world.b.org_id) == before_b


@pytest.mark.parametrize("repo", ["farms", "producers", "lots", "messages", "events",
                                  "alerts", "reports", "conversations"])
def test_no_se_puede_forzar_organization_id_en_add_ni_update(
    repo: str, world: World, repos: Repositories
) -> None:
    r = getattr(repos, repo)
    with pytest.raises(ProtectedFieldError):
        r.add(world.a.org_id, organization_id=world.b.org_id)
    own_id = {
        "farms": world.a.farm_id, "producers": world.a.producer_id, "lots": world.a.lot_id,
        "messages": world.a.message_id, "events": world.a.event_id,
        "alerts": world.a.alert_id, "reports": world.a.report_id,
        "conversations": world.a.conversation_id,
    }[repo]
    with pytest.raises(ProtectedFieldError):
        r.update(world.a.org_id, own_id, organization_id=world.b.org_id)


def test_a_si_ve_lo_suyo(world: World, repos: Repositories) -> None:
    """Control positivo: el filtro no es tan estricto que A no vea sus propios datos."""
    a = world.a
    assert repos.organizations.get(a.org_id).id == a.org_id
    assert repos.farms.get(a.org_id, a.farm_id) is not None
    assert repos.producers.get_by_phone(a.org_id, a.phone).id == a.producer_id
    assert repos.lots.get_by_code(a.org_id, "ESP-01").id == a.lot_id
    assert repos.lots.get_by_public_token(a.org_id, a.public_token).id == a.lot_id
    assert [l.id for l in repos.lots.list_by_farm(a.org_id, a.farm_id)] == [a.lot_id]
    assert repos.messages.get_by_external_id(a.org_id, a.external_id).id == a.message_id
    assert [e.id for e in repos.events.list_by_lot(a.org_id, a.lot_id)] == [a.event_id]
    assert [x.id for x in repos.alerts.list_pending(a.org_id)] == [a.alert_id]
    assert [x.id for x in repos.alerts.list_open(a.org_id, lot_id=a.lot_id)] == [a.alert_id]
    assert [p.id for p in repos.producers.list_by_farm(a.org_id, a.farm_id)] == [a.producer_id]
    assert [r.id for r in repos.reports.list_by_lot(a.org_id, a.lot_id)] == [a.report_id]
    assert repos.conversations.get_by_producer(a.org_id, a.producer_id).id == a.conversation_id
    assert repos.conversations.get_or_create(a.org_id, a.producer_id).id == a.conversation_id
    for repo in ("farms", "producers", "lots", "messages", "events", "alerts", "reports",
                 "conversations"):
        rows = getattr(repos, repo).list(a.org_id)
        assert len(rows) == 1 and rows[0].organization_id == a.org_id, repo


def test_resumen_por_lote_no_incluye_lotes_de_b(world: World, repos: Repositories) -> None:
    """summary_by_lot devuelve ids, no filas: se revisa aparte que no traiga lotes de B."""
    repos.events.confirm(world.b.org_id, world.b.event_id)
    repos.events.confirm(world.a.org_id, world.a.event_id)
    assert set(repos.events.summary_by_lot(world.a.org_id)) == {world.a.lot_id}
    assert [lt.id for lt in repos.lots.list_all(world.a.org_id)] == [world.a.lot_id]
