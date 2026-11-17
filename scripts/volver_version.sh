#!/usr/bin/env bash
# Vuelve a la versión desplegada anterior (o a una que se indique).
#
# Uso:
#   scripts/volver_version.sh               # la penúltima de deploy/historial.log
#   scripts/volver_version.sh sha-1a2b3c4   # una concreta (debe estar en el historial)
#
# Si la versión nueva trajo migraciones, primero las deshace con la imagen ACTUAL
# (la vieja no conoce las revisiones nuevas) y después despliega la versión vieja.
# Deshacer una migración puede borrar datos de columnas o tablas nuevas: si eso
# no es aceptable, use el respaldo "antes-de-<tag>" con scripts/restaurar_db.sh.

source "$(dirname "$0")/lib_despliegue.sh"

[[ -f "$HISTORIAL" ]] || fallar "No hay $HISTORIAL: no se sabe cuál era la versión anterior."

ACTUAL="$(valor_env IMAGE_TAG)"
if [[ -n "${1:-}" ]]; then
    LINEA="$(awk -F'\t' -v t="$1" '$2 == t' "$HISTORIAL" | tail -n1)"
    [[ -n "$LINEA" ]] || fallar "La versión $1 no está en el historial."
else
    # La última línea distinta de la versión actual.
    LINEA="$(awk -F'\t' -v t="$ACTUAL" '$2 != t' "$HISTORIAL" | tail -n1)"
    [[ -n "$LINEA" ]] || fallar "El historial no tiene una versión anterior a $ACTUAL."
fi
DESTINO="$(cut -f2 <<< "$LINEA")"
REV_DESTINO="$(cut -f3 <<< "$LINEA")"
REV_ACTUAL="$(revision_actual)"

[[ "$DESTINO" != "latest" ]] || fallar "'latest' no sirve para volver atrás: use un tag sha-..."

info "Volver de $ACTUAL a $DESTINO"
if [[ "$REV_ACTUAL" != "$REV_DESTINO" ]]; then
    aviso "La base está en la revisión $REV_ACTUAL y $DESTINO espera $REV_DESTINO."
    aviso "Se van a DESHACER migraciones; pueden perderse datos de lo que agregó la versión nueva."
    read -r -p "¿Seguir? Escriba SI: " RESPUESTA
    [[ "$RESPUESTA" == "SI" ]] || fallar "Cancelado."
    info "Respaldo antes de deshacer migraciones"
    scripts/respaldo_db.sh --solo-local --prefijo "antes-de-volver-a-$DESTINO" | tail -n1
    info "Deteniendo api, worker y beat"
    docker compose stop api worker beat
    info "alembic downgrade $REV_DESTINO (con la imagen $ACTUAL)"
    docker compose run --rm api alembic downgrade "$REV_DESTINO" \
        || fallar "No se pudo deshacer. Opción: scripts/restaurar_db.sh con el respaldo antes-de-$ACTUAL."
fi

exec scripts/desplegar.sh "$DESTINO"
