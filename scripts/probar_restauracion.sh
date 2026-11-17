#!/usr/bin/env bash
# Prueba que un respaldo se puede restaurar, SIN tocar la base de producción.
#
# Uso:
#   scripts/probar_restauracion.sh /var/backups/bitacora-cafe/bitacora_cafe_diario_....dump
#   ESTRICTO=1 scripts/probar_restauracion.sh archivo.dump   # falla si los conteos no cuadran
#
# Qué hace:
#   1. Levanta un PostgreSQL desechable (misma versión que producción).
#   2. Restaura el archivo con --exit-on-error: cualquier error hace fallar la prueba.
#   3. Revisa que exista la revisión de Alembic.
#   4. Cuenta las filas de cada tabla y las compara con la base actual, si está corriendo.
#      Si el respaldo es de antes, es normal que la base actual tenga más filas;
#      con ESTRICTO=1 (lo usa la CI) deben ser iguales.
#   5. Borra el PostgreSQL desechable.

source "$(dirname "$0")/lib_despliegue.sh"

ARCHIVO="${1:-}"
[[ -f "$ARCHIVO" ]] || fallar "Uso: $0 <archivo.dump>"
ESTRICTO="${ESTRICTO:-0}"
IMAGEN="${IMAGEN_POSTGRES:-postgres:17-alpine}"
NOMBRE="bitacora-prueba-restauracion-$$"

trap 'docker rm -f "$NOMBRE" >/dev/null 2>&1 || true' EXIT

info "Levantando PostgreSQL desechable ($IMAGEN)"
docker run -d --name "$NOMBRE" -e POSTGRES_PASSWORD=desechable "$IMAGEN" > /dev/null
for _ in $(seq 1 60); do
    docker exec "$NOMBRE" pg_isready -U postgres -q 2>/dev/null && break
    sleep 1
done
# pg_isready responde antes de que termine el arranque inicial; se espera la base real.
for _ in $(seq 1 30); do
    docker exec "$NOMBRE" psql -U postgres -tAc 'SELECT 1' >/dev/null 2>&1 && break
    sleep 1
done
docker exec "$NOMBRE" createdb -U postgres restaurada

info "Restaurando $(basename "$ARCHIVO")"
docker exec -i "$NOMBRE" pg_restore -U postgres -d restaurada --no-owner --no-acl --exit-on-error < "$ARCHIVO" \
    || fallar "La restauración falló."

psql_prueba() { docker exec -i "$NOMBRE" psql -v ON_ERROR_STOP=1 -X -q -tA -U postgres -d restaurada; }

REV="$(echo "SELECT version_num FROM alembic_version" | psql_prueba | head -n1 || true)"
[[ -n "$REV" ]] || fallar "El respaldo no tiene revisión de Alembic."
info "Revisión de Alembic en el respaldo: $REV"

# Una consulta por tabla, generada con \gexec. Salida: tabla|filas
SQL_CONTEOS="SELECT format('SELECT %L || ''|'' || count(*) FROM public.%I', tablename, tablename)
FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename \gexec"

RESTAURADA="$(echo "$SQL_CONTEOS" | psql_prueba)"
[[ -n "$RESTAURADA" ]] || fallar "El respaldo restaurado no tiene tablas."

ACTUAL=""
if db_corriendo; then
    ACTUAL="$(echo "$SQL_CONTEOS" | psql_app || true)"
fi

DIFERENCIAS=0
printf '\n%-28s %12s %12s\n' "tabla" "respaldo" "base actual"
printf '%-28s %12s %12s\n' "-----" "--------" "-----------"
while IFS='|' read -r tabla filas; do
    actual="$(grep -E "^${tabla}\|" <<< "$ACTUAL" | cut -d'|' -f2 || true)"
    marca=""
    if [[ -n "$ACTUAL" && "$filas" != "${actual:-}" ]]; then
        marca="  ≠"
        DIFERENCIAS=$((DIFERENCIAS + 1))
    fi
    printf '%-28s %12s %12s%s\n' "$tabla" "$filas" "${actual:--}" "$marca"
done <<< "$RESTAURADA"
echo

if [[ "$ESTRICTO" == "1" ]]; then
    [[ -n "$ACTUAL" ]] || fallar "ESTRICTO=1 necesita la base actual corriendo para comparar."
    [[ $DIFERENCIAS -eq 0 ]] || fallar "$DIFERENCIAS tabla(s) con conteos distintos."
fi
info "Restauración probada: OK"
