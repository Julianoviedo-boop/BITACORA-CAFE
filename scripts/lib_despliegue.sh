# shellcheck shell=bash
# Funciones compartidas por los scripts de despliegue y respaldo.
# Se carga con: source "$(dirname "$0")/lib_despliegue.sh"
# Todos los scripts corren desde la raíz del repositorio, donde están el .env y
# los archivos de docker compose.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

# shellcheck disable=SC2034  # lo usan desplegar.sh y volver_version.sh
HISTORIAL="$RAIZ/deploy/historial.log"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
aviso() { printf '\033[1;33m[aviso]\033[0m %s\n' "$*" >&2; }
fallar() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

# Valor de una variable: primero el entorno, si no el .env. Sin comillas alrededor.
valor_env() {
    local clave="$1" defecto="${2:-}" valor=""
    if [[ -n "${!clave:-}" ]]; then
        printf '%s' "${!clave}"
        return
    fi
    if [[ -f .env ]]; then
        valor="$(grep -E "^${clave}=" .env | tail -n1 | cut -d= -f2- || true)"
        valor="${valor%\"}"; valor="${valor#\"}"
        valor="${valor%\'}"; valor="${valor#\'}"
    fi
    printf '%s' "${valor:-$defecto}"
}

# Cambia (o agrega) CLAVE=valor en el .env.
fijar_env() {
    local clave="$1" valor="$2"
    if grep -qE "^${clave}=" .env; then
        sed -i "s|^${clave}=.*|${clave}=${valor}|" .env
    else
        printf '%s=%s\n' "$clave" "$valor" >> .env
    fi
}

db_corriendo() {
    [[ -n "$(docker compose ps --status running -q db 2>/dev/null)" ]]
}

# SQL en la base de la aplicación, dentro del contenedor db (sin contraseña: socket local).
psql_app() {
    docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -X -q -tA -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
}

# Revisión de Alembic de la base, o "ninguna" si no hay base o no está migrada.
revision_actual() {
    if ! db_corriendo; then
        echo ninguna
        return
    fi
    local rev
    rev="$(echo "SELECT version_num FROM alembic_version" | psql_app 2>/dev/null | head -n1 || true)"
    echo "${rev:-ninguna}"
}

# Espera a que la api quede healthy (máximo $1 segundos, 240 por defecto).
esperar_api() {
    local limite="${1:-240}" inicio estado id
    inicio=$(date +%s)
    while true; do
        id="$(docker compose ps -q api 2>/dev/null || true)"
        estado="$( [[ -n "$id" ]] && docker inspect --format '{{.State.Health.Status}}' "$id" 2>/dev/null || echo ausente)"
        [[ "$estado" == "healthy" ]] && return 0
        if (( $(date +%s) - inicio > limite )); then
            aviso "La api no quedó healthy en ${limite}s (estado: $estado). Últimos logs:"
            docker compose logs --tail 40 api >&2 || true
            return 1
        fi
        sleep 5
    done
}
