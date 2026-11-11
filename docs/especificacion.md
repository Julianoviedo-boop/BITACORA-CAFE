# Especificación — Bitácora de Café (v1)

## 1. Problema
El caficultor hace muchas labores en sus lotes (abonar, fumigar, desyerbar, recolectar, beneficiar, secar) pero casi nunca las anota. Sin ese registro no hay trazabilidad para certificaciones, compradores de cafés especiales ni para tomar mejores decisiones.

## 2. Flujo principal
1. **Mensaje entrante.** El caficultor manda un audio o un texto por WhatsApp, por ejemplo: *"Hoy abonamos el lote La Esperanza con 4 bultos de 17-6-18"*.
2. **Webhook.** OpenBSP llama al webhook. El webhook valida la firma, guarda el mensaje crudo con su `message_id` (para no procesarlo dos veces), encola una tarea en Celery y responde `200` de inmediato.
3. **Transcripción.** Si es audio, el worker lo descarga y lo transcribe con faster-whisper (CPU, modelo small), usando `config/vocabulario.txt` como pista de términos cafeteros.
4. **Extracción.** El texto se envía a Claude con salida estructurada. Los tipos de evento y sus campos se definen en `config/eventos.yaml`. Resultado: tipo de evento, lote, fecha, insumos, cantidades, unidades y campos faltantes.
5. **Resolución del lote.** Se busca el lote dentro de la organización del usuario (por nombre o alias). Si no se encuentra o hay varios parecidos, se le pregunta.
6. **Confirmación.** Se le responde por WhatsApp con un resumen sencillo:
   *"Entendí esto: Abonada en La Esperanza, hoy, 4 bultos de 17-6-18. ¿Está bien? Responda SÍ o NO."*
   - **SÍ** → el evento pasa a *confirmado*.
   - **NO** → se descarta o se pide que lo corrija.
   - Si faltan datos obligatorios, se preguntan antes de pedir confirmación.
7. **Historia del lote.** Los eventos confirmados forman la línea de tiempo del lote.
8. **Alertas.** Según `config/alertas.yaml` (ej.: días de carencia tras una fumigación antes de recolectar), se avisa al caficultor.
9. **Reporte de trazabilidad.** Desde el panel web se genera el reporte por lote y rango de fechas.

## 3. Estados de un evento
`borrador` → `pendiente_confirmacion` → `confirmado` | `descartado`

Solo los eventos `confirmado` aparecen en la historia y en los reportes.

## 4. Alcance de la primera versión
- Recepción de audios y textos por WhatsApp vía OpenBSP.
- Webhook idempotente con procesamiento en Celery + Redis.
- Transcripción local con faster-whisper.
- Extracción estructurada con Claude según `config/eventos.yaml`.
- Confirmación obligatoria por WhatsApp antes de guardar.
- Multi-organización: cada finca/asociación es una organización y todo se filtra por `organization_id`.
- Gestión básica de fincas, lotes y usuarios (teléfonos autorizados).
- Alertas simples definidas en `config/alertas.yaml`.
- Panel web (Next.js) para ver la historia de cada lote y descargar el reporte de trazabilidad.
- Todo lo que ve el usuario, en español de Colombia y con lenguaje sencillo.

## 5. Fuera de alcance (v1)
- **Fotos** (de plagas, del lote, de facturas).
- **Predicción de puntaje** de taza o de calidad.
- **Sensores** (clima, humedad, IoT).
- **Pagos** (cobros, suscripciones, pagos a trabajadores).
- Integración con sistemas de certificadoras o compradores.
- App móvil propia (el canal es solo WhatsApp).

## 6. Criterios de éxito
- El webhook responde en menos de 1 segundo.
- Un mensaje repetido por OpenBSP no crea eventos duplicados.
- Ningún evento queda confirmado sin el "SÍ" del usuario.
- La extracción acierta en el tipo de evento y el lote en la mayoría de los casos del set de prueba (`scripts/evaluar_extraccion.py`).
