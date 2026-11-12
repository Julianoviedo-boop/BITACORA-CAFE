# Bitácora de Café

El caficultor manda audios o textos por WhatsApp sobre lo que hizo en un lote; el sistema transcribe, extrae los datos, pide confirmación y arma la historia del lote. Ver [CLAUDE.md](CLAUDE.md) y [docs/](docs/).

## Requisitos

- Docker Desktop (o Docker Engine) con Docker Compose v2.20 o superior.

## Arrancar en local

```bash
cp .env.example .env        # y cambie las contraseñas
docker compose up -d --build
docker compose ps           # api, db y redis deben quedar "healthy"
```

El primer build tarda varios minutos (faster-whisper y sus dependencias). El modelo de Whisper se guarda en el volumen `hf_cache`, así que solo se descarga una vez y sobrevive a los rebuilds.

## Servicios

Todos los puertos quedan ligados a `127.0.0.1` (no se exponen a la red).

| Servicio | URL | Para qué |
|---|---|---|
| api | http://localhost:8000 · [docs](http://localhost:8000/docs) | FastAPI con recarga automática |
| web | http://localhost:3000 | Panel en Next.js |
| worker | — | Celery (misma imagen base que la api, etapa `worker`) |
| db | localhost:5432 | PostgreSQL (volumen `pg_data`) |
| redis | localhost:6379 | Broker y resultados de Celery |
| adminer | http://localhost:8081 | Ver la base de datos (servidor: `db`) |
| flower | http://localhost:5555 | Monitorear tareas de Celery |
| dozzle | http://localhost:8888 | Ver los logs de todos los contenedores |
| tunnel | — | Túnel de Cloudflare hacia `api:8000` (perfil `tunnel`) |

## Comprobar que todo funciona

```bash
# Salud: revisa base de datos y Redis (200 si todo bien, 503 si algo falla)
curl http://localhost:8000/health
# {"status":"ok","checks":{"db":"ok","redis":"ok"}}

# Encolar la tarea "ping" y esperar la respuesta del worker
docker compose exec api python -c "from app.worker.tasks import ping; print(ping.delay().get(timeout=10))"
# pong

# Pruebas del backend (usan la base bitacora_cafe_test, que se crea sola)
docker compose exec -e REQUIRE_DB_TESTS=1 api pytest
```

## Base de datos

```bash
docker compose exec api alembic upgrade head          # aplicar migraciones
docker compose exec api python /scripts/seed.py       # datos de ejemplo (2 organizaciones)
docker compose exec api alembic revision --autogenerate -m "describa el cambio"
docker compose exec api alembic check                 # ¿los modelos y las migraciones coinciden?
```

Los endpoints y las tareas no consultan la base directamente: usan los repositorios de `app/db/repositories/`, y todos sus métodos exigen `organization_id`.

## Comandos útiles

```bash
docker compose logs -f api worker          # logs
docker compose up -d --scale worker=3      # más workers
docker compose --profile tunnel up -d      # abrir el túnel público
docker compose logs tunnel | grep trycloudflare   # ver la URL del túnel
docker compose down                        # apagar (los datos quedan en los volúmenes)
docker compose down -v                     # apagar y BORRAR datos y caché de modelos
```

El túnel usa un "quick tunnel" de Cloudflare: la URL `*.trycloudflare.com` cambia cada vez que arranca. Para una URL fija, cree un túnel con nombre en Cloudflare y cambie el comando por `tunnel --no-autoupdate run --token ...` con el token en el `.env`.

## Producción

`docker-compose.prod.yml` solo trae las diferencias: imágenes finales sin recarga ni código montado, adminer/flower/dozzle sin puertos publicados y `restart: unless-stopped`.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

## Estructura

```
backend/   FastAPI, Celery, SQLAlchemy (Dockerfile multietapa: dev, api, worker)
web/       Panel en Next.js + Tailwind (Dockerfile: dev y prod standalone)
config/    Reglas de negocio en YAML (se montan en /config, solo lectura)
docs/      Especificación y decisiones
```
