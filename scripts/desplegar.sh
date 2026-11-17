#!/usr/bin/env bash
# Despliega una versión (tag de imagen de GHCR) en el servidor.
#
# Uso:
#   scripts/desplegar.sh sha-1a2b3c4      # una versión concreta (recomendado)
#   scripts/desplegar.sh latest           # lo último de main (no sirve para volver atrás)
#
# Pasos:
#   1. Descarga las imágenes de esa versión.
#   2. Si ya hay datos, saca un respaldo local "antes-de-<tag>".
#   3. Aplica las migraciones con la imagen nueva (si fallan, no cambia nada).
#   4. Guarda IMAGE_TAG en el .env, recrea los contenedores y espera /health.
#   5. Anota la versión y la revisión de Alembic en deploy/historial.log
#      (lo usa scripts/volver_version.sh).

source "$(dirname "$0")/lib_despliegue.sh"

TAG="${1:-}"
[[ -n "$TAG" ]] || fallar "Uso: $0 <tag>   (p. ej. sha-1a2b3c4; ver GitHub → Packages)"
[[ -f .env ]] || fallar "No hay .env en $RAIZ"
[[ "$(valor_env COMPOSE_FILE)" == *prod* ]] \
    || fallar "El .env no tiene COMPOSE_FILE=docker-compose.prod.yml: ¿está en el servidor?"

ANTERIOR="$(valor_env IMAGE_TAG)"
export IMAGE_TAG="$TAG"

info "Descargando imágenes $TAG (antes: ${ANTERIOR:-ninguna})"
docker compose pull --quiet

REV_ANTES="$(revision_actual)"
if [[ "$REV_ANTES" != "ninguna" ]]; then
    info "Respaldo antes de desplegar"
    scripts/respaldo_db.sh --solo-local --prefijo "antes-de-$TAG" | tail -n1
fi

info "Aplicando migraciones con la imagen $TAG"
docker compose run --rm api alembic upgrade head \
    || fallar "Las migraciones fallaron. Los contenedores siguen con ${ANTERIOR:-la versión anterior}."

fijar_env IMAGE_TAG "$TAG"

info "Recreando contenedores"
docker compose up -d --remove-orphans
esperar_api || fallar "La versión $TAG no quedó healthy. Para volver: scripts/volver_version.sh"

REV_DESPUES="$(revision_actual)"
mkdir -p "$(dirname "$HISTORIAL")"
printf '%s\t%s\t%s\n' "$(date -Iseconds)" "$TAG" "$REV_DESPUES" >> "$HISTORIAL"

docker image prune -f > /dev/null
info "Desplegada $TAG (Alembic: $REV_ANTES → $REV_DESPUES)"
