# Despliegue en un VPS

Guía paso a paso para poner Bitácora de Café en un servidor Linux con Docker Compose. Ver la decisión D-019 en [decisiones.md](decisiones.md).

## Cómo queda

```
Internet ──► 80/443 ──► Caddy ──┬──► api:8000  (api.su-dominio.com, incluye el webhook)
                      (HTTPS)   └──► web:3000  (panel.su-dominio.com: panel con clave
                                                y /l/<token>, la página pública del lote)

Solo en 127.0.0.1 del servidor (se entra por túnel SSH):
   adminer :8081   flower :5555   dozzle :8888

Sin puertos: db (PostgreSQL), redis, worker, beat
Respaldo diario: pg_dump ──► disco del servidor (7 días) ──► rclone cifrado ──► almacenamiento externo (30 días)
```

- En el servidor **no se compila nada**. GitHub Actions construye las imágenes en cada push a `main` y las sube a GHCR con el tag `sha-<commit>`. El servidor las descarga.
- Del repositorio, el servidor solo usa `docker-compose.prod.yml`, `deploy/`, `scripts/` y `config/` (las reglas de negocio se montan desde ahí).
- Los secretos viven solo en el `.env` del servidor.
- El QR del reporte PDF lleva a `https://<WEB_DOMAIN>/l/<token>`: `PUBLIC_WEB_URL` se arma solo con `WEB_DOMAIN` (D-023). Si cambia el dominio, los QR ya impresos dejan de funcionar.

## 0. Lo que necesita antes de empezar

| Qué | Para qué |
|---|---|
| Un VPS con **Ubuntu 24.04**, 4 vCPU y 8 GB de RAM (mínimo 2 vCPU y 4 GB) y 40 GB de disco | Whisper en CPU usa cerca de 1 GB por proceso del worker |
| Un dominio con dos subdominios: `api.` y `panel.` | Caddy saca un certificado para cada uno |
| Una cuenta de almacenamiento compatible con S3 (Backblaze B2, Cloudflare R2, Wasabi…) | Las copias de seguridad, fuera del servidor |
| El repositorio en GitHub con la rama `main` | Las imágenes las publica `.github/workflows/publicar.yml` |
| Las llaves de OpenBSP y de Anthropic | Ver `.env.example` |

En los comandos, `SERVIDOR` es la IP del VPS y `su-dominio.com` su dominio.

## 1. Crear el servidor

1. En el proveedor (Hetzner, DigitalOcean, Vultr, Contabo…) cree el VPS con Ubuntu 24.04 y **su llave SSH pública** (no use contraseña). Si el proveedor tiene firewall de red, deje entrar solo 22, 80 y 443 (TCP) y 443 (UDP).
2. Entre como root y cree el usuario `deploy`:

   ```bash
   ssh root@SERVIDOR
   adduser --disabled-password --gecos "" deploy
   usermod -aG sudo deploy
   echo 'deploy ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/deploy   # o póngale contraseña con passwd
   rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy
   ```

3. Desde **otra terminal**, compruebe que entra: `ssh deploy@SERVIDOR`. Solo entonces cierre el acceso de root y por contraseña:

   ```bash
   sudo tee /etc/ssh/sshd_config.d/10-endurecer.conf <<'EOF'
   PermitRootLogin no
   PasswordAuthentication no
   KbdInteractiveAuthentication no
   EOF
   sudo systemctl restart ssh
   ```

4. Actualizaciones de seguridad automáticas y zona horaria:

   ```bash
   sudo apt update && sudo apt -y upgrade
   sudo apt -y install unattended-upgrades git rclone curl
   sudo dpkg-reconfigure -f noninteractive unattended-upgrades
   sudo timedatectl set-timezone America/Bogota
   ```

## 2. Instalar Docker

Desde el repositorio oficial de Docker (el paquete `docker.io` de Ubuntu suele traer un Compose viejo):

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker deploy
```

Salga y vuelva a entrar por SSH (para que tome el grupo `docker`) y compruebe:

```bash
docker run --rm hello-world
docker compose version    # 2.20 o superior
```

## 3. Firewall

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw enable
sudo ufw status verbose
```

> **Ojo con Docker y ufw.** Docker abre los puertos que publica un contenedor **por encima de ufw**. Por eso en `docker-compose.prod.yml` solo Caddy publica en `0.0.0.0` (80 y 443); adminer, flower y dozzle publican en `127.0.0.1`, y db, redis, api, worker y web no publican nada. Una prueba automática (`backend/tests/test_despliegue.py`) falla si alguien agrega un puerto público. Si cambia el compose, revise desde **otra máquina**:
>
> ```bash
> nmap -Pn -p 1-10000 SERVIDOR    # solo deben salir 22, 80 y 443
> ```

## 4. DNS

En el proveedor del dominio cree dos registros **A** (y **AAAA** si el VPS tiene IPv6):

| Nombre | Tipo | Valor |
|---|---|---|
| `api` | A | IP del servidor |
| `panel` | A | IP del servidor |

Espere a que respondan antes del primer arranque (si no, Caddy no puede sacar los certificados):

```bash
dig +short api.su-dominio.com
dig +short panel.su-dominio.com
```

## 5. Código y variables

```bash
sudo mkdir -p /opt/bitacora-cafe /var/backups/bitacora-cafe
sudo chown deploy:deploy /opt/bitacora-cafe /var/backups/bitacora-cafe
git clone https://github.com/<usuario>/BITACORA-CAFE.git /opt/bitacora-cafe
cd /opt/bitacora-cafe
git checkout main
cp .env.example .env
chmod 600 .env
nano .env
```

En el `.env` del servidor:

1. **Quite el `# `** de las líneas de la sección *Producción* y llénelas:
   - `COMPOSE_FILE=docker-compose.prod.yml` (así `docker compose` usa producción sin escribir `-f`).
   - `GHCR_OWNER`: su usuario de GitHub **en minúsculas**.
   - `API_DOMAIN`, `WEB_DOMAIN`, `ACME_EMAIL`.
   - `IMAGE_TAG` puede quedar como está: lo escribe `scripts/desplegar.sh`.
2. `APP_ENV=production`.
3. Contraseñas y secretos nuevos. Genere cada uno con:

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   - `APP_SECRET_KEY`
   - `POSTGRES_PASSWORD` **y la misma** dentro de `DATABASE_URL`
   - `OPENBSP_WEBHOOK_SECRET` y `OPENBSP_WEBHOOK_TOKEN` (sin ellos, en producción la API no arranca; D-018)
   - `PANEL_API_TOKEN`, y `PANEL_USUARIO` y `PANEL_CLAVE` para entrar al panel (sin ellos el panel no abre; D-020).
   - `PANEL_ORGANIZATION_ID` **déjelo vacío por ahora**: es el id de la organización, que todavía no existe. Se llena en el paso 6.
4. `WHATSAPP_CHANNEL=openbsp` y las llaves de OpenBSP.
5. `ANTHROPIC_API_KEY`. Ponga también un límite de gasto mensual en la consola de Anthropic.
6. Deje `RESPALDO_REMOTO` para el paso 9.

### Acceso a las imágenes de GHCR

Los paquetes de GHCR nacen **privados**. Dos opciones:

- **Hacerlos públicos** (el código ya es público si el repo lo es): GitHub → su perfil → *Packages* → `bitacora-cafe-backend` → *Package settings* → *Change visibility* → *Public*. Lo mismo con `bitacora-cafe-web`. No hace falta login.
- **Dejarlos privados:** cree un token *classic* en GitHub (*Settings → Developer settings → Personal access tokens*) **solo** con el permiso `read:packages`, y en el servidor:

  ```bash
  docker login ghcr.io -u <usuario>    # pegue el token como contraseña
  ```

## 6. Primer arranque

1. En GitHub → *Actions* → *Publicar imágenes*, abra la última ejecución verde de `main`. El resumen dice el tag, por ejemplo `scripts/desplegar.sh sha-1a2b3c4`.
2. En el servidor:

   ```bash
   cd /opt/bitacora-cafe
   scripts/desplegar.sh sha-1a2b3c4
   ```

   El script descarga las imágenes, aplica las migraciones, guarda `IMAGE_TAG` en el `.env`, levanta todo, espera a que `/health` responda bien y anota la versión en `deploy/historial.log`.
3. Compruebe:

   ```bash
   docker compose ps                                   # api healthy; caddy, worker, beat, web Up
   curl https://api.su-dominio.com/health              # {"status":"ok",...}
   docker compose logs caddy | grep -i certificate     # "certificate obtained successfully"
   ```

4. Registre la organización con su número de WhatsApp. `--numero` es el *phone_number_id* de Meta (el `organization_address` de OpenBSP, no el teléfono); `--telefono` es el de un productor que va a escribir. En producción `scripts/` no está montado en la api, por eso va `-v`:

   ```bash
   docker compose run --rm -v "$PWD/scripts:/scripts:ro" api \
     python /scripts/registrar_organizacion.py --numero <phone_number_id> \
     --nombre "Asociación ..." --telefono +57300... --productor "Nombre del productor"
   ```

   La primera línea dice `Organización <id>: ...`. Ponga ese id en `PANEL_ORGANIZATION_ID` del `.env` y recree la api para que lo lea:

   ```bash
   nano .env
   docker compose up -d --force-recreate api
   ```

5. Abra `https://panel.su-dominio.com` y entre con `PANEL_USUARIO` y `PANEL_CLAVE`:
   - En el inicio debe decir "Todo está funcionando".
   - En *Lotes* debe aparecer el lote `PRU-01`. Si dice "no está bien configurado", `PANEL_API_TOKEN` o `PANEL_ORGANIZATION_ID` están mal.
   - En el lote, *Reporte en PDF* debe abrir el PDF. Escanee el QR con el celular: debe abrir la página pública **sin pedir clave**.
6. En OpenBSP, registre el webhook:
   - URL: `https://api.su-dominio.com/webhooks/openbsp/<OPENBSP_WEBHOOK_SECRET>`
   - Token: el valor de `OPENBSP_WEBHOOK_TOKEN`.
7. Pruebe con el teléfono del productor registrado: acepte la autorización de datos, mande un audio ("hoy abonamos el lote PRU-01 con 4 bultos de 17-6-18"), responda SÍ y luego escriba `REPORTE LOTE PRU-01`. Debe llegar el PDF como documento. Es la primera prueba real del envío de documentos por OpenBSP (D-023): si no llega, vea *Problemas frecuentes*.
8. El primer audio tarda más: el worker descarga el modelo de Whisper (queda en el volumen `hf_cache`).

> `scripts/seed.py` carga datos **de ejemplo**: no lo corra en producción.

## 7. Migraciones

`scripts/desplegar.sh` las aplica **con la imagen nueva antes de cambiar los contenedores**: si fallan, la versión anterior sigue corriendo y no cambia nada. Además la api corre `alembic upgrade head` al arrancar y `/health` responde 503 si la base no está en la última migración.

Comandos a mano:

```bash
docker compose run --rm api alembic current          # en qué revisión está la base
docker compose run --rm api alembic history          # todas las revisiones
docker compose run --rm api alembic upgrade head     # aplicar pendientes
```

## 8. Herramientas internas por túnel SSH

Adminer, Flower y Dozzle solo escuchan en `127.0.0.1` del servidor. Desde **su computador**:

```bash
ssh -N -L 8081:127.0.0.1:8081 -L 5555:127.0.0.1:5555 -L 8888:127.0.0.1:8888 deploy@SERVIDOR
```

Deje esa terminal abierta y entre a:

| Herramienta | URL en su computador | Notas |
|---|---|---|
| Adminer | http://localhost:8081 | Servidor `db`, usuario y contraseña del `.env` |
| Flower | http://localhost:5555 | Tareas de Celery |
| Dozzle | http://localhost:8888 | Logs de todos los contenedores |

Para cerrar el acceso basta con cerrar el túnel (Ctrl+C).

## 9. Copias de seguridad

`scripts/respaldo_db.sh` hace `pg_dump` (formato custom, comprimido), comprueba que el archivo se pueda leer, lo guarda en `/var/backups/bitacora-cafe` (7 días), lo sube **cifrado** con rclone (30 días) y borra los viejos en los dos lados.

### 9.1 Almacenamiento externo (ejemplo con Backblaze B2)

1. Cree un bucket **privado**, por ejemplo `bitacora-cafe-respaldos`.
2. Cree una *Application Key* con acceso **solo a ese bucket** (lectura y escritura). Anote `keyID` y `applicationKey`.

Con Cloudflare R2, Wasabi o S3 es igual: un bucket privado y una llave limitada a él (en rclone escoja el tipo `s3` y el proveedor).

### 9.2 Configurar rclone con cifrado

Los respaldos tienen datos personales de los productores (D-013): se cifran antes de salir del servidor.

```bash
rclone config
```

1. `n` (nuevo remoto) → nombre `b2` → tipo `b2` → `account` = keyID, `key` = applicationKey → el resto por defecto.
2. `n` otra vez → nombre `bitacora-cifrado` → tipo `crypt` → `remote` = `b2:bitacora-cafe-respaldos` → `filename_encryption` = `standard` → escoja **generar** una contraseña fuerte y también la segunda (*salt*).
3. **Guarde las dos contraseñas del cifrado en su gestor de contraseñas, fuera del servidor.** Si el servidor se pierde y no las tiene, los respaldos no sirven.

Pruebe:

```bash
echo prueba > /tmp/prueba.txt && rclone copy /tmp/prueba.txt bitacora-cifrado:postgres
rclone ls bitacora-cifrado:postgres      # se ve prueba.txt
rclone ls b2:bitacora-cafe-respaldos     # se ve un nombre cifrado
rclone delete bitacora-cifrado:postgres --include prueba.txt
```

En el `.env`: `RESPALDO_REMOTO=bitacora-cifrado:postgres`.

### 9.3 Programarlo todos los días

```bash
cd /opt/bitacora-cafe
scripts/respaldo_db.sh                     # primera vez a mano: debe terminar sin errores
sudo cp deploy/systemd/bitacora-respaldo.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bitacora-respaldo.timer
systemctl list-timers bitacora-respaldo.timer     # próxima ejecución: 3:15 a. m.
```

Ver cómo salió: `journalctl -u bitacora-respaldo -n 50`.

**Recomendado:** cree un chequeo gratis en [healthchecks.io](https://healthchecks.io) (periodo 1 día, gracia 2 horas) y ponga su URL en `RESPALDO_AVISO_URL`. Si un día el respaldo falla, le llega un correo.

## 10. Probar la restauración (una vez al mes)

Un respaldo que nunca se ha restaurado no es un respaldo. `scripts/probar_restauracion.sh` restaura el archivo en un PostgreSQL desechable, **sin tocar producción**, revisa la migración y compara las filas de cada tabla con la base actual:

```bash
cd /opt/bitacora-cafe
mkdir -p /tmp/restauracion
rclone copy bitacora-cifrado:postgres /tmp/restauracion --max-age 36h    # el más reciente, desde afuera
scripts/probar_restauracion.sh "$(ls -t /tmp/restauracion/*.dump | head -n1)"
rm -rf /tmp/restauracion
```

Salida esperada (con más filas en la base actual si el respaldo es de anoche):

```
==> Revisión de Alembic en el respaldo: d5a2c7e1f8b9

tabla                            respaldo  base actual
alembic_version                         1            1
farms                                   3            3
inbound_messages                      412          430  ≠
...
==> Restauración probada: OK
```

La CI hace la misma prueba en cada pull request (respaldo → restauración → conteos iguales).

## 11. Restaurar de verdad

**En el mismo servidor** (se dañó o se borró la base):

```bash
cd /opt/bitacora-cafe
rclone copy bitacora-cifrado:postgres /tmp/restauracion --max-age 36h    # o use uno de /var/backups/bitacora-cafe
scripts/probar_restauracion.sh /tmp/restauracion/<archivo>.dump          # primero, probarlo
scripts/restaurar_db.sh /tmp/restauracion/<archivo>.dump
```

`restaurar_db.sh` pide escribir el nombre de la base, saca un respaldo de lo que hay (`antes-de-restaurar`), detiene api, worker, beat, web y flower, borra y crea la base, restaura, vacía la cola de Celery y arranca todo esperando `/health`.

**En un servidor nuevo** (se perdió el servidor):

1. Pasos 1 a 5 de esta guía. En el `.env` ponga el **mismo `IMAGE_TAG`** que tenía (o el último que funcione) y las mismas llaves.
2. Pegue en `rclone config` las **mismas contraseñas del cifrado** (paso 9.2, pero escogiendo "escribir mi propia contraseña").
3. Restaure y despliegue:

   ```bash
   docker compose up -d db
   rclone copy bitacora-cifrado:postgres /tmp/restauracion --max-age 36h
   echo "<POSTGRES_DB>" | scripts/restaurar_db.sh /tmp/restauracion/<archivo>.dump
   scripts/desplegar.sh <IMAGE_TAG>
   ```

4. Si la IP cambió, actualice el DNS (paso 4).

## 12. Actualizar

1. Haga merge del pull request a `main`. GitHub Actions corre las pruebas y publica las imágenes; el resumen de la ejecución dice el tag nuevo.
2. En el servidor:

   ```bash
   cd /opt/bitacora-cafe
   git pull                                 # trae config/*.yaml, scripts y compose de esa versión
   scripts/desplegar.sh sha-<commit nuevo>
   ```

Antes de migrar, `desplegar.sh` saca un respaldo local `antes-de-sha-...`. Si algo falla en las migraciones, no cambia nada. Si la versión nueva no queda healthy, el script lo dice y muestra los logs.

> Use siempre tags `sha-...`. `latest` sirve para probar, pero con él no se puede volver atrás.

## 13. Volver a la versión anterior

```bash
cd /opt/bitacora-cafe
scripts/volver_version.sh                 # a la versión desplegada justo antes
scripts/volver_version.sh sha-1a2b3c4     # o a una concreta del historial
cat deploy/historial.log                  # fecha, tag y revisión de Alembic de cada despliegue
```

- **Si la versión nueva no tenía migraciones:** solo cambia las imágenes. Toma menos de un minuto.
- **Si tenía migraciones:** el script avisa y pide escribir `SI`. Saca un respaldo, deshace las migraciones con la imagen **actual** (la vieja no las conoce) y despliega la vieja. Deshacer una migración puede perder lo que se guardó en columnas o tablas nuevas. Si eso no es aceptable, en lugar del script restaure el respaldo `antes-de-sha-<nueva>` (paso 11) y luego `scripts/desplegar.sh sha-<vieja>`.
- **Si la versión nueva cambió `config/*.yaml`:** devuelva también la configuración: `git checkout <commit viejo> -- config/` y vuelva a correr `scripts/volver_version.sh` (o `docker compose restart api worker beat`).

## Problemas frecuentes

| Síntoma | Qué revisar |
|---|---|
| Caddy no saca el certificado | `docker compose logs caddy`. El DNS debe apuntar al servidor y los puertos 80 y 443 deben estar abiertos (ufw **y** el firewall del proveedor). |
| La api no queda healthy | `docker compose logs api`. Casi siempre es el `.env`: falta `OPENBSP_WEBHOOK_TOKEN` en producción, la contraseña de `DATABASE_URL` no coincide con `POSTGRES_PASSWORD`, o hay un error en `config/alertas.yaml` (el log dice dónde). |
| `docker compose pull` dice *denied* | Los paquetes de GHCR son privados y falta `docker login ghcr.io` (paso 5), o `GHCR_OWNER` no está en minúsculas. |
| El respaldo no corre | `journalctl -u bitacora-respaldo -n 50`. Revise `RESPALDO_REMOTO` y `rclone ls bitacora-cifrado:postgres`. |
| Se llena el disco | `docker system df` y `docker image prune -a` (borra imágenes que no estén en uso; las versiones viejas se vuelven a descargar si hace falta). Los logs de cada contenedor se rotan a 50 MB. |
| El panel dice "no está bien configurado" | `PANEL_API_TOKEN` debe ser igual para la api y el panel (los dos lo leen del `.env`) y tener 32 caracteres o más; `PANEL_ORGANIZATION_ID` debe ser el id de la organización. Después de cambiarlos: `docker compose up -d --force-recreate api web`. |
| REPORTE responde pero el PDF no llega | `docker compose logs worker \| grep -i reporte`. Si dice "OpenBSP rechazó la subida", OpenBSP no acepta los headers con los que se sube el archivo (no están documentados; D-023): revise el código y el texto de la respuesta. Mientras tanto, el PDF se puede bajar del panel. |
| El QR abre otra dirección | `PUBLIC_WEB_URL` se arma con `WEB_DOMAIN`: revise ese valor en el `.env` y recree api y worker. |
| Los audios tardan mucho | Un worker con `--concurrency=2` usa dos núcleos. En un VPS con más núcleos: `docker compose up -d --scale worker=2`. |
