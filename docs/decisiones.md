# Registro de decisiones

Formato: cada decisión tiene fecha, contexto, decisión y consecuencias. Las nuevas se agregan al final.

---

## D-001 · WhatsApp como único canal de entrada
- **Fecha:** 2026-11-11
- **Contexto:** El caficultor ya usa WhatsApp a diario y prefiere mandar audios a llenar formularios.
- **Decisión:** La captura de datos se hace solo por WhatsApp (audio o texto). El panel web es para consultar y generar reportes.
- **Consecuencias:** Toda la experiencia de registro se diseña como conversación. No hay app móvil.

## D-002 · OpenBSP como proveedor de WhatsApp, aislado en `app/channels/`
- **Fecha:** 2026-11-11
- **Contexto:** Queremos poder cambiar de proveedor sin tocar el resto del sistema.
- **Decisión:** Usar OpenBSP. Todo el código que habla con WhatsApp vive en `app/channels/` detrás de una interfaz propia.
- **Consecuencias:** El dominio no conoce detalles de OpenBSP.

## D-003 · Webhook rápido e idempotente; trabajo pesado en Celery + Redis
- **Fecha:** 2026-11-11
- **Contexto:** Transcribir y extraer toma segundos; los proveedores reintentan si el webhook tarda.
- **Decisión:** El webhook valida, guarda el mensaje con su `message_id` único y encola. Celery hace la transcripción, la extracción y las respuestas.
- **Consecuencias:** Se necesita Redis y un worker. Un reintento del proveedor no duplica eventos.

## D-004 · Transcripción local con faster-whisper (CPU, modelo small)
- **Fecha:** 2026-11-11
- **Contexto:** Costos bajos, privacidad de los audios y sin depender de GPU.
- **Decisión:** faster-whisper con modelo `small` en CPU, con vocabulario cafetero de `config/vocabulario.txt`.
- **Consecuencias:** Audios largos tardan más; aceptable porque el proceso es asíncrono.

## D-005 · Extracción con la API de Claude y salida estructurada (Pydantic)
- **Fecha:** 2026-11-11
- **Contexto:** El lenguaje del caficultor es informal y variado; las reglas fijas no alcanzan.
- **Decisión:** Usar Claude con salida estructurada validada con modelos Pydantic generados a partir de `config/eventos.yaml`.
- **Consecuencias:** Costo por mensaje; se mide la calidad con `scripts/evaluar_extraccion.py`.

## D-006 · Confirmación obligatoria antes de guardar
- **Fecha:** 2026-11-11
- **Contexto:** La transcripción y la extracción pueden equivocarse, y la trazabilidad debe ser confiable.
- **Decisión:** Ningún evento pasa a `confirmado` sin un "SÍ" explícito del usuario por WhatsApp.
- **Consecuencias:** Un paso más para el usuario, a cambio de datos confiables.

## D-007 · Reglas de negocio en `config/*.yaml`
- **Fecha:** 2026-11-11
- **Contexto:** Tipos de evento, insumos y alertas cambian según la finca o la certificación.
- **Decisión:** Se definen en archivos YAML, nunca en el código.
- **Consecuencias:** Se pueden ajustar sin desplegar código nuevo; hay que validar los YAML al arrancar.

## D-008 · Multi-organización con `organization_id` en todas las consultas
- **Fecha:** 2026-11-11
- **Contexto:** Varias fincas o asociaciones compartirán el mismo sistema.
- **Decisión:** Toda tabla de negocio lleva `organization_id` y toda consulta filtra por él.
- **Consecuencias:** Se deben incluir pruebas que verifiquen que una organización no ve datos de otra.

## D-009 · Stack base
- **Fecha:** 2026-11-11
- **Decisión:** FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL en el backend; Next.js + Tailwind + shadcn/ui en el panel; todo orquestado con Docker Compose. Secretos solo por variables de entorno.
