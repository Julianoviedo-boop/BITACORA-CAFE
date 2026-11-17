#!/usr/bin/env bash
# Copia de seguridad de PostgreSQL con pg_dump (formato custom, comprimido).
#
# Uso:
#   scripts/respaldo_db.sh                  # respaldo + subida al almacenamiento externo
#   scripts/respaldo_db.sh --solo-local     # solo en el disco del servidor
#   scripts/respaldo_db.sh --prefijo antes-de-sha-abc1234 --solo-local
#
# Variables (del entorno o del .env):
#   RESPALDO_DIR          carpeta local          (por defecto /var/backups/bitacora-cafe)
#   RESPALDO_DIAS_LOCAL   días que se guardan en el servidor          (por defecto 7)
#   RESPALDO_REMOTO       destino de rclone, p. ej. bitacora-cifrado:postgres
#   RESPALDO_DIAS_REMOTO  días que se guardan afuera                  (por defecto 30)
#   RESPALDO_AVISO_URL    opcional: URL que se llama si todo salió bien (healthchecks.io)
#
# Al final imprime la ruta del archivo en la última línea.

source "$(dirname "$0")/lib_despliegue.sh"

SOLO_LOCAL=0
PREFIJO="diario"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --solo-local) SOLO_LOCAL=1 ;;
        --prefijo) PREFIJO="$2"; shift ;;
        *) fallar "Opción desconocida: $1" ;;
    esac
    shift
done

DIR="$(valor_env RESPALDO_DIR /var/backups/bitacora-cafe)"
DIAS_LOCAL="$(valor_env RESPALDO_DIAS_LOCAL 7)"
REMOTO="$(valor_env RESPALDO_REMOTO)"
DIAS_REMOTO="$(valor_env RESPALDO_DIAS_REMOTO 30)"
AVISO_URL="$(valor_env RESPALDO_AVISO_URL)"

if [[ $SOLO_LOCAL -eq 0 && -z "$REMOTO" ]]; then
    fallar "Falta RESPALDO_REMOTO en el .env (o use --solo-local). Ver docs/despliegue.md."
fi
db_corriendo || fallar "El contenedor db no está corriendo."

umask 077
mkdir -p "$DIR"
ARCHIVO="$DIR/bitacora_cafe_${PREFIJO}_$(date +%Y%m%d_%H%M%S).dump"
TMP="$ARCHIVO.parcial"
trap 'rm -f "$TMP"' EXIT

info "pg_dump → $ARCHIVO"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --compress=6 --no-owner --no-acl' > "$TMP"

# Un respaldo que no se puede leer no sirve: se revisa antes de darlo por bueno.
docker compose exec -T db pg_restore --list < "$TMP" > /dev/null \
    || fallar "El archivo generado no se puede leer con pg_restore."
grep -q . "$TMP" || fallar "El respaldo quedó vacío."
chmod 600 "$TMP"
mv "$TMP" "$ARCHIVO"
info "Listo: $(du -h "$ARCHIVO" | cut -f1)"

if [[ $SOLO_LOCAL -eq 0 ]]; then
    command -v rclone >/dev/null || fallar "rclone no está instalado (sudo apt install rclone)."
    info "Subiendo a $REMOTO"
    rclone copy --no-traverse "$ARCHIVO" "$REMOTO"
    # Confirma que quedó allá con el mismo tamaño.
    rclone check --size-only --one-way --files-from <(basename "$ARCHIVO") "$DIR" "$REMOTO" \
        || fallar "El archivo no quedó bien en $REMOTO."
    info "Borrando en $REMOTO los respaldos de más de $DIAS_REMOTO días"
    rclone delete --min-age "${DIAS_REMOTO}d" --include 'bitacora_cafe_*.dump' "$REMOTO"
fi

info "Borrando en $DIR los respaldos de más de $DIAS_LOCAL días"
find "$DIR" -maxdepth 1 -name 'bitacora_cafe_*.dump' -mtime +"$DIAS_LOCAL" -delete

if [[ -n "$AVISO_URL" ]]; then
    curl -fsS -m 10 --retry 3 "$AVISO_URL" > /dev/null || aviso "No se pudo avisar a RESPALDO_AVISO_URL."
fi

echo "$ARCHIVO"
