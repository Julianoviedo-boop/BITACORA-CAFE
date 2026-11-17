#!/usr/bin/env bash
# Restaura un respaldo ENCIMA de la base de producción. Borra lo que haya.
#
# Uso:
#   scripts/restaurar_db.sh /var/backups/bitacora-cafe/bitacora_cafe_diario_....dump
#
# Antes de usarlo: pruebe el archivo con scripts/probar_restauracion.sh.
# Pasos: respaldo de lo que hay ahora → detiene api, worker, beat y flower →
# borra y crea la base → restaura → vuelve a arrancar y espera /health.

source "$(dirname "$0")/lib_despliegue.sh"

ARCHIVO="${1:-}"
[[ -f "$ARCHIVO" ]] || fallar "Uso: $0 <archivo.dump>"
db_corriendo || fallar "El contenedor db no está corriendo (docker compose up -d db)."

BASE="$(valor_env POSTGRES_DB)"
aviso "Esto BORRA la base '$BASE' y la reemplaza con $(basename "$ARCHIVO")."
read -r -p "Escriba el nombre de la base para seguir: " RESPUESTA
[[ "$RESPUESTA" == "$BASE" ]] || fallar "Cancelado."

if [[ "$(revision_actual)" != "ninguna" ]]; then
    info "Respaldo de seguridad de la base actual"
    scripts/respaldo_db.sh --solo-local --prefijo antes-de-restaurar | tail -n1
fi

info "Deteniendo api, worker, beat, web y flower"
docker compose stop api worker beat web flower

info "Borrando y creando la base"
docker compose exec -T db sh -c '
    psql -v ON_ERROR_STOP=1 -X -q -U "$POSTGRES_USER" -d postgres \
        -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\" WITH (FORCE)" \
        -c "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_USER\""'

info "Restaurando"
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-acl --exit-on-error' < "$ARCHIVO" \
    || fallar "La restauración falló. La base quedó incompleta: restaure otro archivo o el respaldo 'antes-de-restaurar'."

# Las tareas en cola pueden apuntar a filas que ya no existen.
info "Vaciando la cola de Celery"
docker compose exec -T redis redis-cli FLUSHALL > /dev/null

info "Arrancando de nuevo"
docker compose up -d
esperar_api || fallar "La api no arrancó después de restaurar. Revise: docker compose logs api"
info "Restauración terminada. Revisión de Alembic: $(revision_actual)"
