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

## D-010 · JSONB para los datos de cada evento (`lot_events.data`)
- **Fecha:** 2026-11-12
- **Contexto:** Cada tipo de evento trae campos distintos: una abonada tiene insumo, cantidad y unidad; una fumigación, producto, dosis y días de carencia; una recolección, kilos de cereza y número de recolectores. Esos tipos y sus campos se definen en `config/eventos.yaml` (D-007) y van a cambiar con el uso, por finca o por certificación.
- **Decisión:** Las columnas comunes a todos los eventos son columnas normales (`lot_id`, `event_type`, `occurred_at`, `status`, `confidence`, `original_text`, `message_id`). Los campos propios de cada tipo van en una sola columna `data` de tipo JSONB.
- **Por qué JSONB y no otra cosa:**
  - **Frente a una tabla o columnas por tipo de evento:** agregar un tipo o un campo en `eventos.yaml` no exige una migración ni desplegar código. Si el esquema dependiera del YAML, cada ajuste agronómico se volvería un cambio de base de datos, que es justo lo que D-007 quiere evitar.
  - **Frente a un modelo entidad‑atributo‑valor (una fila por campo):** un evento se lee y se escribe en una sola fila, sin uniones; los valores conservan su tipo (número, texto, lista) y la historia del lote sale con una consulta simple.
  - **Frente a JSON o TEXT:** JSONB se guarda ya analizado, permite índices GIN y operadores como `data @> '{"insumo": "17-6-18"}'` para buscar, por ejemplo, todos los lotes donde se aplicó un producto. Se creó el índice `ix_lot_events_data` (GIN, `jsonb_path_ops`) para eso.
  - **Encaja con la extracción:** Claude devuelve un objeto validado con Pydantic (D-005); se guarda tal cual, sin traducirlo a columnas.
- **Consecuencias:**
  - La base no valida la forma de `data`: la validación es responsabilidad del código, con modelos Pydantic generados desde `eventos.yaml`, **antes** de guardar. Ningún dato entra a `data` sin pasar por esa validación.
  - Si un campo se vuelve crítico para consultas frecuentes o para reglas de alertas (p. ej. días de carencia), se puede promover a columna o a índice de expresión sin perder lo histórico.
  - Al cambiar un campo en `eventos.yaml`, los eventos viejos conservan la forma con que se guardaron; los reportes deben tolerar campos ausentes.

## D-011 · Aislamiento entre organizaciones en dos capas: repositorios y FKs compuestas
- **Fecha:** 2026-11-12
- **Contexto:** D-008 exige filtrar todo por `organization_id`. Un solo olvido en una consulta expone datos de otra organización.
- **Decisión:**
  1. **Repositorios** (`app/db/repositories/`): son el único acceso a la base desde endpoints y tareas. Todo método público recibe `organization_id` como primer argumento y falla si no es un UUID. No se pueden asignar `organization_id`, `id` ni timestamps desde `add()`/`update()`.
  2. **Base de datos:** cada tabla de negocio tiene `UNIQUE (organization_id, id)` y las referencias entre tablas son FKs compuestas `(organization_id, x_id)`. PostgreSQL rechaza, por ejemplo, un evento de la organización A sobre un lote de B aunque el código tenga un error.
  3. Los modelos **no** declaran `relationship()`: la carga perezosa haría consultas por fuera del filtro.
- **Consecuencias:** `tests/test_aislamiento_organizaciones.py` ataca cada método público de cada repositorio con datos de otra organización y verifica que no devuelve ni modifica nada; una meta‑prueba falla si se agrega un método sin su caso. Las FKs usan `ON DELETE RESTRICT` (borrar en cascada entre tablas se decide caso por caso).
- **Pendiente:** el enlace público del reporte (`lots.public_token`) se consulta hoy dentro de una organización; la página pública necesitará un método de solo lectura que busque por token sin `organization_id`, como excepción explícita y probada.

## D-012 · Estados de un evento: `pendiente`, `confirmado`, `rechazado`
- **Fecha:** 2026-11-12
- **Contexto:** La especificación inicial tenía `borrador`, `pendiente_confirmacion`, `confirmado` y `descartado`.
- **Decisión:** Se simplifica a tres estados. El evento nace `pendiente`; solo `LotEventRepository.confirm()` o `reject()` lo cambian, una sola vez, y el estado no se puede asignar con `add()` ni `update()` (regla de oro 2). Los datos faltantes se preguntan antes de crear el evento.
- **Consecuencias:** Corregir un evento rechazado se hace creando uno nuevo, lo que deja rastro de ambos.

## D-013 · Conexión con OpenBSP: autenticación, formato y consentimiento
- **Fecha:** 2026-11-12
- **Fuentes:** README.md, MIGRATING_FROM_TWILIO.md e INTEGRATING.md de `matiasbattocchia/open-bsp-api`. Donde la documentación no alcanza se revisó el código fuente y se dice explícitamente.
- **Lo que dice la documentación:**
  - OpenBSP hace `POST {entity, action, data}`; `data` es la fila de `messages` tal cual. El webhook también llega con los mensajes propios (`sender_address` null) y, si se suscribe `update`, con los cambios de estado.
  - Para enviar se inserta en `/rest/v1/messages` con los headers `apikey` (llave pública) y `api-key` (llave secreta). `organization_address` es el phone_number_id de Meta, no el teléfono; `conversation_address` va en E.164 sin `+`.
  - Un archivo es `content = {type: "file", kind: "audio" | "image" | ..., file: {mime_type, uri, name, size}, text}`. **Ese ejemplo es para ENVIAR**; no hay ejemplo de un audio o una imagen entrante.
  - Sobre la descarga, MIGRATING_FROM_TWILIO.md solo dice que `content.file.uri` "apunta a una URL firmada de Storage".
  - Autenticación del webhook: si al registrarlo se le pone `token`, llega `Authorization: Bearer <token>`. **No hay firma (HMAC) documentada.**
- **Lo que muestra el código (no está documentado):**
  - El aviso sale de un trigger (`notify_webhook`) con `pg_net`: manda la fila cruda, **una sola vez, sin reintentos y sin firma**.
  - En la fila, `file.uri` es `internal://media/organizations/<org>/attachments/<hash>`, no una URL firmada. Esto contradice MIGRATING_FROM_TWILIO.md.
  - La política RLS de `storage.objects` deja descargar con el `api-key` de la organización. La ruta de descarga (`/storage/v1/object/authenticated/media/<llave>`) se dedujo de eso y de la ruta de subida que sí está documentada. **Hay que comprobarla con un audio real.**
- **Decisión:**
  1. Autenticación: un secreto de 32 caracteres o más en la URL (`/webhooks/openbsp/<OPENBSP_WEBHOOK_SECRET>`), comparado en tiempo constante. Si se configura `OPENBSP_WEBHOOK_TOKEN`, también se exige el `Bearer`. El secreto se borra de los logs de acceso de uvicorn.
  2. Como OpenBSP no reintenta, el webhook responde 200 a todo aviso auténtico, aunque lo ignore. Si Redis falla al encolar, el mensaje queda guardado en `recibido` (falta una tarea que recoja esos mensajes).
  3. La organización se identifica por el número receptor (`organizations.whatsapp_address`). Es una búsqueda sin `organization_id`, como excepción explícita (`app/db/repositories/routing.py`) que solo devuelve el id. Si el número no es de ninguna organización, no se guarda nada. Si el teléfono no es de un productor registrado, tampoco.
  4. Se descargan `internal://media/...` y URLs https del mismo host que `OPENBSP_BASE_URL`. Cualquier otra referencia se rechaza (así nadie nos hace descargar de cualquier URL).
  5. Consentimiento (Ley 1581 de 2012): mientras el productor no responda SÍ **por escrito**, sus mensajes no se procesan: quedan en `sin_consentimiento`, se borra su contenido y se le vuelve a pedir la autorización. El SÍ o el NO se guarda como constancia. Los textos y las respuestas válidas están en `config/consentimiento.yaml`.
  6. Los mensajes de grupos se ignoran.
- **Consecuencias:** `WHATSAPP_CHANNEL=fake` permite probar todo sin WhatsApp (`scripts/simular_mensaje.py`). Antes de producción hay que capturar un webhook real con audio e imagen, guardarlo en `tests/fixtures/` y confirmar la descarga.

## D-014 · Transcripción: decodificación propia, modelo por proceso y límite de 3 minutos
- **Fecha:** 2026-11-12
- **Contexto:** D-004 eligió faster-whisper `small` en CPU. Al implementarlo y probar con un OGG/Opus real salió que `faster_whisper.decode_audio` (1.2.1, la última versión) llama a `av.open(metadata_errors=...)`, que PyAV 16+ ya no acepta: ningún audio se podía decodificar.
- **Decisión:**
  1. El audio se decodifica con PyAV directamente (`app/transcription/whisper.py`, `decodificar`), sin pasar por `decode_audio`. Los frames dañados se saltan.
  2. El modelo (CPU, `int8`, elegido con `WHISPER_MODEL`) se carga una vez por proceso del worker y se precarga en `worker_process_init`.
  3. Los audios de más de `max_duracion_segundos` (180, en `config/transcripcion.yaml`) no se transcriben: se le pide al productor que los mande más cortos. Los avisos también están en ese archivo.
  4. Las fallas temporales de descarga se reintentan 5 veces (30 s a 300 s). Si se acaban, o la falla es definitiva, el mensaje queda en `error` y otra tarea (`avisar_productor`) le avisa al productor, para que una falla al enviar el aviso no obligue a transcribir otra vez.
  5. Se mantiene `small`. Comparación con `scripts/comparar_whisper.py` (5 audios **sintéticos**, 70,5 s de audio, contenedor con 4 núcleos):

     | Modelo | Tiempo total | Veces el tiempo real | WER | Términos cafeteros | RAM por proceso |
     |---|---:|---:|---:|---:|---:|
     | small  | 137,9 s | 1,96 | 0,0 % | 26/26 | ~0,8 GB |
     | medium | 373,2 s | 5,29 | 0,5 % | 25/26 | ~3,0 GB |

- **Consecuencias:**
  - Los errores medidos no sirven todavía para decidir: los audios son de voz sintética (es-ES) y casi no tienen errores. Hay que repetir la comparación con audios reales de productores (con su `.txt` de referencia en `backend/tests/fixtures/`).
  - En esta máquina `small` tarda casi el doble de lo que dura el audio: un audio de 3 minutos tardaría unos 6 minutos. Falta ajustar `WHISPER_CPU_THREADS` frente a `--concurrency` y medir en el servidor de producción.
  - Con `medium` y `--concurrency=2`, el worker necesitaría unos 6 GB solo para los modelos.
