# Bitácora de Café - Claude Code Guidelines

## Objetivo
Sistema donde el caficultor manda audios o texto por WhatsApp sobre lo que hizo en un lote de café; el sistema transcribe, extrae datos estructurados, pide confirmación y construye la historia del lote con reporte de trazabilidad.

## Stack Tecnológico
- **Backend:** FastAPI, SQLAlchemy 2, Alembic, PostgreSQL
- **Cola de tareas:** Celery + Redis
- **Transcripción:** faster-whisper (en CPU, modelo small)
- **Extracción:** API de Claude con salida estructurada (Pydantic)
- **Frontend Panel:** Next.js + Tailwind + shadcn/ui
- **Entorno:** Docker Compose
- **Canal:** OpenBSP (WhatsApp)

## Estructura de carpetas
```
backend/
  app/
    api/            # Endpoints FastAPI (incluye el webhook)
    channels/       # Único lugar que habla con WhatsApp (OpenBSP)
    db/             # Modelos SQLAlchemy y sesión
    domain/         # Reglas y casos de uso (lotes, eventos, confirmaciones)
    transcription/  # faster-whisper
    extraction/     # Llamadas a Claude con salida estructurada
    reports/        # Reporte de trazabilidad del lote
    worker/         # Tareas de Celery
  alembic/          # Migraciones
  tests/fixtures/   # Audios y textos de ejemplo para pruebas
web/                # Panel en Next.js
config/
  eventos.yaml      # Tipos de evento y sus campos
  alertas.yaml      # Reglas de alertas
  vocabulario.txt   # Términos cafeteros para ayudar a la transcripción
scripts/            # Utilidades (simular mensajes, evaluar extracción)
docs/               # Especificación y decisiones
```

## Reglas de Oro (Obligatorias)
1. Toda consulta a la base de datos debe filtrar obligatoriamente por `organization_id`.
2. Nada se guarda como evento confirmado sin la confirmación explícita del usuario por WhatsApp.
3. El webhook debe responder rápido y delegar el procesamiento pesado en Celery. Debe ser idempotente.
4. Las interacciones con WhatsApp solo se programan dentro de `app/channels/`.
5. Las reglas de negocio del café viven en archivos dentro de `config/*.yaml`, nunca quemadas en el código.
6. Los secretos y credenciales se manejan exclusivamente por variables de entorno (`.env`).
7. Toda función nueva debe incluir su respectiva prueba unitaria/integración.
8. La interfaz de usuario debe estar completamente en español de Colombia, con lenguaje simple y directo.
