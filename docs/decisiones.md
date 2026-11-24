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

## D-015 · Extracción: esquema genérico de valores, normalización en código y Haiku por defecto
- **Fecha:** 2026-11-12
- **Contexto:** D-005 pide salida estructurada con modelos Pydantic generados de `config/eventos.yaml`. La API compila el esquema a una gramática y limita a 24 parámetros opcionales y 16 con tipos unión (cada `X | None` cuenta) en todo el esquema.
- **Decisión:**
  1. Cada evento trae los campos comunes (`tipo_evento`, `fragmento`, `lote_mencionado`, `codigo_lote`, `fecha`, `hora`, `faltantes`) y una lista `valores: [{campo, valor, unidad}]`, con `campo`, `unidad` y `faltantes` como enums generados del YAML. Así el esquema tiene 6 uniones y 0 opcionales, y no crece en uniones al agregar campos. Un modelo por tipo de evento con un campo anulable por dato se pasaría del límite.
  2. La salida de Claude se normaliza en código (`app/extraction/extractor.py`): un `codigo_lote` que no es de un lote activo de la organización queda en null, una fecha posterior al mensaje queda en null, se quitan los campos que no son del tipo, se revisan números y unidades, y `faltantes` se recalcula con los obligatorios del catálogo.
  3. Solo se le pasan a Claude los lotes activos de la organización del mensaje (`LotRepository.list_active`).
  4. El texto del productor va dentro de `<mensaje_productor>`, con `<` y `>` neutralizados, y se trata como datos.
  5. Modelo `claude-haiku-5-5` con esfuerzo `medium` (`ANTHROPIC_MODEL`, `ANTHROPIC_EFFORT`). El system prompt es fijo por catálogo y se cachea.
- **Consecuencias:**
  - Medición con `scripts/evaluar_extraccion.py` (Haiku 5.5, `medium`, dos corridas por fila). El grupo `base` (20 casos) se usó para encontrar los errores. El grupo `validacion` (12 casos) se escribió antes de cambiar el prompt, e incluye casos donde las reglas nuevas NO deben aplicar. Cambios al prompt: labores encadenadas heredan lote y fecha, número de lote por código o nombre (ambiguo si los dos apuntan a lotes distintos), y sin referencia de tiempo la fecha es null.

    | Prompt | Grupo | Lote | Fecha | Faltantes | Valores | Inventados |
    |---|---|---:|---:|---:|---:|---:|
    | inicial | base | 87 % / 87 % | 100 % / 100 % | 87 % / 87 % | 100 % | 0 |
    | inicial | validacion | 75 % / 81 % | 88 % / 94 % | 62 % / 75 % | 100 % | 0 |
    | con cambios | base | 100 % / 100 % | 100 % / 100 % | 100 % / 100 % | 100 % | 0 |
    | con cambios | validacion | 100 % / 100 % | 100 % / 100 % | 100 % / 100 % | 100 % | 0 |

    Con `low` y el prompt nuevo: fecha 100 % (con el inicial asumía "hoy"), lote 92 % (no resuelve números por código). Se queda `medium`. Unos US$ 0,004 a 0,008 por corrida de 32 casos con caché.
  - Con 100 % en los dos grupos, el set ya no distingue entre versiones del prompt: hace falta agregar casos más difíciles antes del próximo ajuste.
  - Esa medición es optimista: `eventos.yaml` y los casos son un borrador escrito por el equipo de desarrollo y los textos son las referencias de los audios sintéticos, no transcripciones reales. Hay que agregar mensajes reales de productores.

## D-016 · Conversación: estado en la base, de a una pregunta y correcciones sobre el pendiente
- **Fecha:** 2026-11-13
- **Contexto:** Después de extraer hay que cerrar el ciclo con el productor por WhatsApp: preguntar lo que falte, mostrar un resumen corto, y aceptar SÍ, correcciones (por audio o texto) o la cancelación. El worker se puede reiniciar entre un mensaje y el siguiente, y dos mensajes del mismo productor pueden caer en workers distintos.
- **Decisión:**
  1. **Estado en la tabla `conversations`**, una fila por productor: estado (`libre`, `preguntando`, `ofreciendo_lote`, `confirmando`), borradores de los eventos (JSONB), ids de los eventos pendientes, pregunta abierta, rondas de corrección y lo último que se le preguntó. Nada vive en memoria del worker.
  2. **Tres pasos por mensaje** (`app/domain/conversacion.py`): leer (transacción corta) → Claude (sin transacción) → aplicar (transacción con la fila bloqueada `FOR UPDATE`). Una columna `version` detecta si otro worker cambió la conversación mientras se hablaba con Claude; en ese caso no se toca nada y se vuelve a empezar (hasta 3 veces; después reintenta Celery).
  3. **Borradores y eventos:** mientras falte un dato obligatorio, el evento vive solo como borrador en la conversación (D-012: se pregunta antes de crear el evento). Cuando está completo se crea el `lot_event` *pendiente* y se manda el resumen. Una corrección **actualiza** ese pendiente (no crea otro); si la corrección quita un evento, el que sobra queda *rechazado*.
  4. **Correcciones y respuestas con Claude:** `extraccion.revisar` manda los eventos propuestos, lo último que se le preguntó y el mensaje nuevo, y pide la lista completa corregida. Las instrucciones van en el mensaje del usuario, no en el system, para no cambiar el prompt medido en D-015 ni su caché. La salida pasa por la misma normalización.
  5. **Sin Claude** para SÍ, NO, CANCELAR y los comandos (`mis lotes`, `resumen lote 3`, `ayuda`): se comparan con las listas de `config/conversacion.yaml`, sin tildes ni signos. Solo un SÍ de esa lista, en estado `confirmando` (o sea, después de ver el resumen), confirma (regla de oro 2). Un emoji solo (👍) no confirma.
  6. **Límites:** máximo 3 correcciones después del resumen y máximo 3 veces la misma pregunta (`max_rondas`). Después se le pide hablar con el asesor; los eventos quedan *pendiente* para que el asesor los revise y la conversación se libera.
  7. **Lote que no existe:** si Claude no pudo ubicar el lote pero el productor lo nombró, se le ofrece crearlo ("el 7" → "Lote 7", código `LOTE-007` si está libre) en su finca. Si el productor no tiene finca y la organización tiene varias, se pasa al asesor.
  8. **Eventos sin lote:** `lot_events.lot_id` admite null para los tipos con `requiere_lote: false` (una venta). La FK compuesta se sigue verificando cuando hay lote.
  9. **`lot_events.data`** guarda `{campo: {valor, unidad}}`, solo lo que el productor dijo. Para buscar: `data @> '{"insumo": {"valor": "17-6-18"}}'` (el ejemplo de D-010 era plano).
  10. **Ventana de 24 horas:** antes de responder se revisa (`app/channels/ventana.py`) que el último mensaje del productor tenga menos de 24 horas. Si no, la respuesta no se manda: queda en `conversations.unsent_reply`, marcada `PENDIENTE(tarea de alertas)`, para que esa tarea la mande con una plantilla aprobada.
- **Consecuencias:**
  - El prompt de revisión **no está medido** con Claude real: `scripts/evaluar_extraccion.py` solo cubre la extracción inicial. Hay que agregar casos de corrección y de respuestas cortas ("4 bultos", "el lunes") antes de producción.
  - Los textos de la conversación tutean ("Responde SÍ o dime qué cambiar"), como pidió el equipo; los de la autorización de datos (D-013) tratan de usted. Conviene unificarlos.
  - Las respuestas de la autorización de datos y los avisos de transcripción todavía no revisan la ventana de 24 horas.
  - Los eventos que quedan *pendiente* tras pasar al asesor necesitan una vista en el panel.

## D-017 · Alertas: etapa calculada, una abierta por regla y envío según la ventana de 24 horas
- **Fecha:** 2026-11-13
- **Contexto:** Hay que avisarle al productor cuando un dato confirmado sale de rango (temperatura en fermentación, humedad en secado) o cuando un lote lleva demasiado tiempo en una etapa sin un evento (volteo, medición de humedad). Las reglas las edita alguien que no programa, y los valores de esta primera versión son **provisionales** hasta que Brayan los valide.
- **Decisión:**
  1. **`config/alertas.yaml`** con cuatro partes comentadas: `etapas`, `alertas_por_valor`, `alertas_por_tiempo` y `plantilla`. Se valida con Pydantic **y contra `eventos.yaml`** al arrancar la API, el worker y beat: si un evento o un campo no existe, el proceso no arranca y el error dice dónde está (`alertas_por_valor → regla 2 (secado / humedad) → campo: ...`), en español y con todos los errores juntos. Sin esa revisión, una regla mal escrita nunca saltaría y nadie se daría cuenta.
  2. **La etapa del lote no se guarda: se calcula** con sus eventos confirmados. Cada etapa dice con qué eventos empieza (`empieza_con`) y, opcional, con cuáles termina (`termina_con`, que deja al lote sin etapa). Una condición puede exigir que un campo de texto contenga el comienzo de una palabra (`dice: ferment`), sin tildes ni mayúsculas. Manda el último evento que cumpla alguna; si uno cumple varias, gana la etapa que va más abajo en el YAML (el orden del proceso).
  3. **Alertas por valor:** se revisan cuando el productor confirma (regla de oro 2: un pendiente nunca dispara nada). Cuenta la etapa en la que se tomó el dato: la que empieza con el evento o, si no empieza ninguna, la de justo antes, aunque el evento la termine (la humedad final del secado se midió en el secado). Los bordes del rango son válidos. Si el campo se mide en varias unidades, la regla debe decir `unidad:`.
  4. **Alertas por tiempo:** Celery beat corre `revisar_alertas_por_tiempo` cada `ALERTAS_REVISION_MINUTOS` (30 por defecto), organización por organización y cada una en su transacción. El plazo se cuenta desde el último evento esperado dentro de la etapa o, si no hay, desde la entrada a la etapa. También se revisan al confirmar, para cerrar la alerta en cuanto llega el evento esperado.
  5. **Eventos sin hora:** quedan a las 00:00 (D-016). Para los plazos se cuentan desde que se confirmaron, sin pasar del final de ese día; si no, "hoy empecé a fermentar" dicho a las 9 a.m. alertaría al mediodía.
  6. **Una alerta abierta por regla y lote.** Estados: `pendiente` (abierta, sin enviar), `enviada` (abierta), `resuelta` (se cerró sola) y `descartada` (desde el panel). Un índice único parcial sobre `(organization_id, lot_id, rule_code)` de las abiertas lo garantiza aunque dos workers revisen a la vez (`INSERT ... ON CONFLICT DO NOTHING`). `rule_code` se arma solo (`valor:secado:humedad`, `tiempo:fermentacion:volteo`).
  7. **Cierre automático:** por tiempo, cuando llega el evento esperado o el lote sale de la etapa. Por valor, cuando una medición **posterior** queda dentro del rango, o cuando el lote vuelve a empezar esa etapa (otra tanda). Una alerta por valor **no** se cierra porque el lote salga de la etapa: la humedad final fuera de norma hay que verla.
  8. **Envío** (`enviar_alerta`, encolada después del commit): a cada productor de la finca del lote que autorizó el tratamiento de datos. Dentro de su ventana de 24 horas (`conversations.last_inbound_at`) va texto; fuera, la plantilla de `alertas.yaml`, con el mensaje como variable `{{1}}`. **Sin plantilla configurada (hoy no hay una aprobada en Meta), la alerta queda `pendiente` y solo en el panel.** No se reintenta después: se intenta una vez, cuando se abre.
  9. **Robustez:** el motor corre en un savepoint dentro de la confirmación; si falla, la confirmación del productor sigue y el error queda en el log. Un número que el canal rechaza no impide avisarles a los demás; una falla temporal reintenta la tarea completa.
  10. **Eventos provisionales en `eventos.yaml`:** `volteo`, `medicion_fermentacion` (con `temperatura`) y `medicion_humedad`, más la unidad `grado_celsius`, para que las reglas de prueba funcionen.
- **Consecuencias:**
  - **Revisar con Brayan:** los rangos y plazos, los eventos provisionales y si `volteo` y las mediciones deben ser eventos aparte o datos de `beneficio` y `secado`.
  - **Plantilla de OpenBSP sin verificar:** la documentación (`MIGRATING_FROM_TWILIO.md`) y los tipos del código no coinciden. Se usa la forma de los tipos (`language.policy: "deterministic"` y `components: [{type: "body", parameters}]`). Hay que probarla con un número real antes de llenar `plantilla.nombre`.
  - Si en una finca hay varios productores y el canal falla de forma temporal a mitad del envío, al reintentar los primeros pueden recibir la alerta dos veces.
  - Un lote que nunca registra el fin de una etapa sigue en ella: su alerta por tiempo queda abierta (una sola) hasta que pase algo. Falta un tope de duración por etapa si eso molesta.
  - El panel todavía no lista alertas ni deja descartarlas; una alerta por valor sin medición posterior queda abierta hasta la siguiente tanda.
  - Sigue pendiente mandar con plantilla las respuestas de la conversación que quedaron en `conversations.unsent_reply` (D-016, punto 10).
  - Debe correr **un solo** beat. Si hubiera dos, cada revisión correría dos veces (no repite alertas, pero trabaja doble).


## D-018 · Seguridad: token del webhook obligatorio en producción, límites de uso y audio largo
- **Fecha:** 2026-11-16
- **Contexto:** Una auditoría de seguridad encontró tres riesgos medios: (1) OpenBSP no firma sus webhooks y el token del header era opcional, así que quien viera la URL del webhook podía hacerse pasar por cualquier productor y confirmar sus eventos (regla de oro 2); (2) un teléfono registrado podía mandar miles de mensajes sin tope y disparar el gasto de la API de Claude u ocupar la cola de todas las organizaciones; (3) el audio se decodificaba completo antes de revisar su duración, y un archivo de 20 MB en Opus puede durar horas y llenar la memoria del worker.
- **Decisión:**
  1. **Webhook:** con `APP_ENV=production` se exigen las dos credenciales, `OPENBSP_WEBHOOK_SECRET` (URL) y `OPENBSP_WEBHOOK_TOKEN` (header `Authorization: Bearer`), ambas de al menos 32 caracteres. Sin ellas la API no arranca, y si cambiaran en caliente se rechazan todos los avisos. En desarrollo el token sigue siendo opcional.
  2. **Límites de uso** (`config/limites.yaml`, `app/domain/limites.py`): topes por productor (30/hora, 150/día) y por organización (300/hora, 2.000/día), en horas y días de Colombia. Se revisan en `procesar_mensaje`, después del consentimiento y antes de transcribir o llamar a Claude; el webhook no cambia. Se cuentan en Redis con un conjunto de ids de mensaje (un reintento no cuenta doble). Primero se cuenta al productor y solo si está dentro de su tope se cuenta para la organización, para que uno que se pasa no les gaste el cupo a los demás. Al pasarse, el mensaje queda en `error` (`limite_productor_hora`...) y se le avisa una sola vez por ventana. Si Redis no responde, se deja pasar y queda en el log.
  3. **Texto máximo:** `max_caracteres_texto` (4.000). Más largo, no va a Claude: queda en `error` (`texto_muy_largo`) y se le pide mandarlo por partes.
  4. **Techo de tokens por respuesta de Claude:** `max_tokens_respuesta` (8.000, antes 16.000 fijo en el código).
  5. **Audio:** `decodificar` cuenta muestras mientras decodifica y se detiene apenas pasa `max_duracion_segundos`. No se confía en la duración que declara el archivo.
- **Consecuencias:**
  - **Antes de desplegar:** generar `OPENBSP_WEBHOOK_TOKEN` con `python -c "import secrets; print(secrets.token_urlsafe(48))"`, ponerlo en el `.env` del servidor y en el campo `token` del webhook en OpenBSP.
  - Los topes son una primera estimación (un reporte normal usa de 2 a 6 mensajes). Ajustarlos con el uso real; el log dice cuándo alguien los alcanza.
  - Si con 8.000 tokens una respuesta se corta, el log dice "La respuesta quedó cortada (max_tokens)": subir el valor.
  - Sigue siendo recomendable poner un límite de gasto mensual en la consola de Anthropic.
  - Todas las organizaciones siguen compartiendo una sola cola de Celery; los topes reducen el problema pero no lo eliminan.

## D-019 · Despliegue: VPS con Docker Compose, Caddy, imágenes de GHCR y respaldo cifrado
- **Fecha:** 2026-11-17
- **Contexto:** Hay que poner el sistema en un servidor propio, con HTTPS para la api (el webhook de OpenBSP) y el panel, sin exponer las herramientas internas, con copias de seguridad fuera del servidor y con una forma segura de actualizar y de volver atrás. El equipo es pequeño: nada de Kubernetes.
- **Decisión:**
  1. **`docker-compose.prod.yml` independiente** (ya no se combina con `docker-compose.yml`). Combinar dos archivos con `!reset`/`!override` hacía difícil saber qué quedaba publicado. El `.env` del servidor trae `COMPOSE_FILE=docker-compose.prod.yml` para que `docker compose` funcione sin `-f`.
  2. **Caddy** es el único servicio con puertos públicos (80, 443 TCP y UDP) y saca los certificados solo. No registra accesos: la URL del webhook lleva el secreto. Adminer, Flower y Dozzle publican en `127.0.0.1` y se usan por túnel SSH. Como Docker abre puertos por encima de ufw, `tests/test_despliegue.py` falla si otro servicio publica un puerto hacia afuera.
  3. **Imágenes construidas en GitHub Actions** y publicadas en GHCR con `sha-<commit>` y `latest`, solo después de que pase la CI. Una sola imagen de backend sirve para api, worker y beat (cambia el comando). El servidor no compila. Sin secretos en el repositorio: la publicación usa el `GITHUB_TOKEN` del workflow.
  4. **`scripts/desplegar.sh <tag>`:** respaldo local → migraciones con la imagen nueva (si fallan no cambia nada) → recrear → esperar `/health` → anotar tag y revisión de Alembic en `deploy/historial.log`.
  5. **`scripts/volver_version.sh`:** vuelve al tag anterior del historial. Si la base cambió de revisión, deshace las migraciones con la imagen **actual** (la vieja no las conoce) después de un respaldo y de pedir confirmación.
  6. **Respaldo diario** con `pg_dump` (formato custom) por un timer de systemd: se verifica con `pg_restore --list`, 7 días en el servidor y 30 afuera, subido con un remoto **crypt** de rclone (los respaldos tienen datos personales, D-013). Las contraseñas del cifrado se guardan fuera del servidor.
  7. **Restauración probada:** `scripts/probar_restauracion.sh` restaura en un PostgreSQL desechable y compara filas por tabla. La CI lo corre en cada pull request en modo estricto; en el servidor se corre una vez al mes.
- **Consecuencias:**
  - `config/*.yaml` no va en la imagen: se monta desde el clon del repositorio. Al actualizar hay que hacer `git pull`; al volver atrás, si la versión cambió la configuración, `git checkout <commit> -- config/`.
  - Deshacer una migración puede perder datos de columnas o tablas nuevas. La alternativa es restaurar el respaldo `antes-de-sha-...`.
  - Las migraciones se aplican con la api detenida solo en `volver_version.sh`; al actualizar, la api vieja sigue atendiendo mientras migra la nueva. Las migraciones deben ser compatibles hacia atrás durante ese momento (agregar antes de quitar).
  - Un solo servidor: si se cae, no hay servicio hasta restaurarlo (pasos en `docs/despliegue.md`, sección 11). OpenBSP reintenta los webhooks, pero no indefinidamente.
  - `dozzle` usa la etiqueta `latest` y monta el socket de Docker en solo lectura; está solo en `127.0.0.1`.

## D-020 · Panel: lista de lotes e historia, con clave y un token por organización
- **Fecha:** 2026-11-24
- **Contexto:** El panel solo tenía la página de estado. Hay que mostrar los lotes y la historia de cada uno. El panel y la api quedan en internet (D-019) y la historia trae datos de los caficultores (D-013), pero todavía no hay usuarios ni inicio de sesión.
- **Decisión:**
  1. **Endpoints de solo lectura** `GET /panel/lotes` y `GET /panel/lotes/{id}` (`app/api/panel.py`). La lógica está en `app/domain/historia_lote.py`.
  2. **Los llama el servidor de Next.js, nunca el navegador**, con `Authorization: Bearer <PANEL_API_TOKEN>`. El token vale para **una sola organización**, `PANEL_ORGANIZATION_ID`, que la api lee de su propio `.env`: no se puede pedir otra organización cambiando un parámetro (regla de oro 1). Un lote de otra organización da 404. Sin token o sin organización bien configurados, los endpoints responden 503.
  3. **Clave de acceso al panel** con HTTP Basic (`web/src/proxy.ts`, usuario y clave en `PANEL_USUARIO` y `PANEL_CLAVE`). En producción, si faltan, el panel responde 503 en lugar de quedar abierto. En desarrollo se entra sin clave.
  4. **La historia solo muestra eventos confirmados** (regla de oro 2), del más antiguo al más nuevo, con la fecha y la hora de Colombia. Un evento a las 00:00 se muestra sin hora (así quedan los que no la dijeron, D-016).
  5. **Nombres desde la configuración:** la actividad sale de `nombre` en `config/eventos.yaml`, cada dato de su nuevo campo opcional `etiqueta` (si falta, la clave con espacios: `cantidad_cereza` → "Cantidad cereza") y las unidades de `config/conversacion.yaml`. El formato de los números y las unidades es el mismo del resumen que se le manda al caficultor por WhatsApp.
- **Consecuencias:**
  - Un panel por organización: para varias organizaciones hay que levantar un panel con su propio token o, mejor, hacer inicio de sesión con usuarios. Cuando exista, reemplaza este token y la clave Basic.
  - Todos los que entran al panel comparten un usuario y una clave: no se sabe quién vio qué. Cambie la clave si se va alguien del equipo.
  - La lista muestra hasta 500 lotes (el máximo de los repositorios), sin paginar.


## D-021 · Consultas por WhatsApp: "¿cómo va el lote 3?"
- **Fecha:** 2026-11-24
- **Contexto:** El caficultor ya podía escribir RESUMEN LOTE 3, pero lo natural es preguntar "¿cómo va el lote 3?". Esa pregunta llegaba a Claude como si fuera un registro: en el mejor caso respondía "no te entendí"; en el peor, podía armar un evento.
- **Decisión:**
  1. **Lista de preguntas en `config/conversacion.yaml`** (`comandos.consulta`: "cómo va", "cómo está", "qué se ha hecho en", "historia del"...). Responden igual que RESUMEN, **sin llamar a Claude y sin crear eventos**. Como los otros comandos, funcionan en cualquier momento y no cambian el estado de la conversación (si había algo por confirmar, se le recuerda).
  2. **Solo es consulta si lo que sigue es un lote y es corto** (hasta 4 palabras): dice "lote", trae un número o es el nombre o el código de un lote de la organización. Así "¿cómo está usted?" no es una consulta, y "¿cómo está el lote 3? hoy le echamos abono" sigue siendo un registro.
  3. **Saludos al inicio y cortesías al final** (`comandos.saludos`, `comandos.cortesias`) se quitan antes de buscar cualquier comando: "Hola, ¿cómo va el lote 3?" y "mis lotes por favor" funcionan.
  4. **Mensaje corto:** los últimos 5 registros confirmados, el más reciente primero. Si hay más, una línea con el total (`textos.resumen_total`).
- **Consecuencias:**
  - Una pregunta que no esté en la lista o sea más larga ("¿cómo va el secado del lote 3?") sigue yendo a Claude. El prompt ya le pide devolver cero eventos ante una pregunta, así que el caficultor recibe "no te entendí" y no se guarda nada. Si aparecen preguntas frecuentes que no están, se agregan a la lista.
  - Los números dichos con palabras ("el lote tres") no se reconocen como lote. Es la misma limitación que tiene RESUMEN.

## D-022 · Panel completo y página pública del lote (/l/<token>)
- **Fecha:** 2026-11-24
- **Contexto:** El panel (D-020) solo tenía la lista de lotes y la historia confirmada. Faltaban la etapa y las alertas de cada lote, el estado de los eventos, las mediciones en el tiempo, la pantalla de alertas y una página para que un comprador vea la historia del lote sin clave.
- **Decisión:**
  1. **Panel con shadcn/ui** (Next.js + Tailwind 4), paleta de finca: verde profundo y terracota sobre fondo crudo, títulos con serifa. Cada pantalla tiene estado de carga (`loading.tsx`), de error (`error.tsx`) y vacío.
  2. **Lista de lotes:** etapa (calculada con `etapas:` de `config/alertas.yaml`, que ahora acepta `nombre`), último registro y alertas abiertas. La etapa de todos los lotes sale de una sola consulta (`events.list_by_status`).
  3. **Detalle del lote:** línea de tiempo con ícono por tipo de evento y **estado** de cada uno (confirmado, esperando confirmación, cancelado). Cambia D-020.4: el panel ahora muestra también los pendientes y cancelados, marcados; solo los confirmados cuentan en el número de registros, en la etapa y en las gráficas (regla de oro 2).
  4. **Mediciones:** los datos con `grafica: true` en `config/eventos.yaml` (temperatura, humedad, horas de fermentación, porcentaje afectado). Una gráfica por dato y unidad: la temperatura de volteos y mediciones va junta.
  5. **Alertas:** `GET /panel/alertas`, las abiertas de la organización del token, la más reciente primero, con si ya se le avisó al caficultor.
  6. **Página pública** `/l/<token>` (Next.js) y `GET /publico/lotes/{token}` (API), sin clave. El token del lote (256 bits, `lots.public_token`) es la llave. Para hallar la organización hay una tercera excepción explícita a la regla de oro 1 en `app/db/repositories/routing.py`: solo devuelve el id de la organización, y el lote se busca después con `organization_id`. El proxy del panel (`web/src/proxy.ts`) deja pasar `/l/` sin la clave Basic.
  7. **Qué ve el comprador:** solo eventos confirmados de tipos con `publico` (por defecto sí) y solo sus datos con `publico` (por defecto sí). Se marcaron `publico: false` la venta (precios y compradores), los jornales y los recolectores. Nunca salen el texto original del caficultor, nombres ni teléfonos, ni la hora. Un tipo o dato que ya no esté en el catálogo no sale. La página pide a los buscadores no indexarla.
- **Consecuencias:**
  - Antes de mandarle el enlace a un comprador, revisar con Brayan qué datos son aptos: basta con cambiar `publico` en `config/eventos.yaml`, sin tocar código. Hoy salen, por ejemplo, la broca o la roya que se vio en el lote.
  - Si un enlace se comparte por error, `lots.regenerate_public_token` lo invalida (todavía no hay botón en el panel).
  - El token público viaja en la URL: Caddy no registra accesos (D-019), pero quien tenga el enlace ve la historia.
  - Un `notFound()` después de que la página empezó a cargar responde 200 con la pantalla de "no encontramos" (así funciona Next.js con `loading.tsx`); no se filtra nada del lote.

## D-023 · Reporte de trazabilidad en PDF con QR (panel y WhatsApp)
- **Fecha:** 2026-11-24
- **Contexto:** El comprador necesita un documento con la historia del lote que pueda guardar o imprimir, y desde el que llegue a la historia en línea. El caficultor debe poder pedirlo por WhatsApp sin entrar al panel.
- **Decisión:**
  1. **`app/reports/`:** `datos.py` arma el contenido, `pdf.py` lo pasa por una plantilla Jinja2 (`plantillas/reporte.html`) y WeasyPrint lo convierte en PDF A4. El QR (segno, SVG) lleva a `<PUBLIC_WEB_URL>/l/<token>`.
  2. **Contenido, de una a dos páginas:** finca, ubicación, altitud, lote, variedad, proceso, periodo, mediciones clave (para cada dato con `grafica: true`: cuántas mediciones, mínimo, máximo y la última) y la línea de tiempo confirmada.
  3. **El reporte y la página pública salen de la misma función** (`historia_publica_de_lote`): la página no muestra nada que no esté en el reporte. Solo eventos confirmados (regla de oro 2) y solo lo marcado como `publico` en `config/eventos.yaml`. Nada de teléfonos, nombres, texto original, precios, jornales ni horas.
  4. **"No registrado", nunca estimado:** se muestra así cuando falta un dato de la ficha del lote, un dato obligatorio de un evento o una medición clave que nunca se tomó. No se calcula ni se completa nada.
  5. **Fuentes dentro de la imagen:** Fraunces y Source Sans 3 (licencia OFL) en `app/reports/fuentes/`, cargadas con `@font-face`. Una prueba revisa que el PDF solo traiga esas fuentes, incrustadas. Al Dockerfile solo se le agregan Pango y HarfBuzz, que WeasyPrint necesita.
  6. **`GET /lotes/{id}/reporte.pdf`** (opcional `?desde=AAAA-MM-DD&hasta=AAAA-MM-DD`), con el token del panel (D-020): un lote de otra organización da 404. El panel lo descarga por `/lotes/<id>/reporte.pdf`, que lo pide desde el servidor de Next.js para que el token no llegue al navegador.
  7. **WhatsApp: "REPORTE LOTE 3"** (prefijos en `comandos.reporte` de `config/conversacion.yaml`, igual que RESUMEN). La conversación responde enseguida "Ya te mando el reporte…" y, después del commit, la tarea `enviar_reporte` arma el PDF y lo manda como documento. Si el lote no tiene registros confirmados no se manda nada. Si el PDF falla, se le pide que lo vuelva a intentar. Solo se manda dentro de la ventana de 24 horas.
  8. **OpenBSP:** `send_document` sube el archivo a `/storage/v1/object/media/organizations/<org>/attachments/<sha256>` y envía `content = {type: "file", kind: "document", file: {uri: "internal://media/…"}}`. Los headers de la subida no están documentados: se usan los de la descarga (`apikey` + `api-key`).
- **Consecuencias:**
  - **Antes de anunciar REPORTE a los productores:** probar el envío de un documento con un número real. Si OpenBSP rechaza la subida, el log del worker dice el código y el texto de la respuesta.
  - **Falta lo que definió Brayan** sobre qué ve el comprador. Hoy sale lo marcado `publico` (por defecto todo menos ventas, jornales y recolectores). Ajustarlo es cambiar `publico` en `config/eventos.yaml`; el reporte y la página cambian juntos.
  - Un lote con muchos registros puede pasar de dos páginas: la tabla sigue en la página siguiente sin cortar filas. Para un comprador conviene pedir el reporte por periodo (`desde`/`hasta`), que hoy solo se puede desde la URL.
  - En producción `PUBLIC_WEB_URL` se arma con `WEB_DOMAIN`. Si cambia el dominio, los QR de reportes ya impresos dejan de servir.
  - El PDF no se guarda: se genera en cada pedido (la tabla `reports` sigue sin usarse).
