# 04 — Visión (solo Claude multimodal)

Decisión del equipo: **no usamos YOLO ni modelos propios**. Toda la visión la hace Claude (multimodal) vía AWS Bedrock (créditos) o la API de Anthropic. La parte en Python solo captura, recorta y arma el pedido.

## Por qué

- Cero entrenamiento, cero dataset: en 2 horas hay clasificación funcionando.
- Claude entiende "plato a medio comer" sin que le enseñemos; con YOLO había que entrenar un clasificador igual.
- La cámara es fija y cenital, las mesas no se mueven: no necesitamos detección ni tracking en tiempo real. Un estado cada 15–30 s por mesa alcanza (un plato no pasa de lleno a vacío en segundos).

## Trade-offs a tener en cuenta

| Riesgo | Mitigación |
|---|---|
| **Latencia** (1–3 s por llamada) | No importa: muestreamos cada 15–30 s. El loop de cámara no espera la respuesta (llamadas asíncronas). |
| **Costo por llamada** | Mandar recortes chicos por mesa (~512 px de lado), no el frame entero. Modelo Haiku (el más reciente en Bedrock). Solo llamar si el recorte **cambió** (diferencia de píxeles contra el último enviado). Ver cuenta abajo. |
| **Rate limits de Bedrock** | Pedir aumento de cuota el primer día. Agrupar varias mesas en una sola llamada (varias imágenes en un mensaje). |
| **Identidad de cada plato** entre llamadas | Claude no da ids estables. Le pasamos el estado anterior de la mesa en el prompt y le pedimos que mantenga los ids (`p1`, `p2`…) según la posición. Ver prompt. |
| **Respuestas inconsistentes** | Salida JSON forzada (tool use / structured output), `temperature: 0`, y suavizado en el backend (ver abajo). |
| **Depende de internet** | Plan B en la demo: video grabado + respuestas cacheadas, o el simulador. |

Cuenta rápida de costo: con 4 mesas, una llamada cada 20 s solo si hubo cambio, son unas pocas miles de llamadas por día de servicio. Calcular el número real con los precios vigentes de Bedrock para el modelo elegido el día 1 y anotarlo acá; para la hackathon los créditos de AWS sobran.

## Setup de cámara

- Vista **cenital** (mirando recto hacia abajo).
- Demo: celular o webcam en trípode/brazo a ~1,5–2 m arriba de una mesa armada. Celular como cámara IP (IP Webcam / DroidCam) → OpenCV lee la URL.
- **Grabar videos de prueba desde la hora 1** (plato lleno → comiendo → vacío, con manos tapando). Sirven para desarrollar y de respaldo en la demo.
- Luz pareja, sin reflejos fuertes.

## Pipeline

```
frame (cada 1 s) → recortar cada mesa por su zona → ¿cambió? → (cada 15–30 s) Claude → JSON → suavizar → POST al backend
```

1. **Zonas**: polígono/rectángulo por mesa en `vision/config/zones.json` (ver [03-contratos.md](03-contratos.md)), dibujado una vez con `tools/draw_zones.py`.
2. **Recorte**: `cv2` recorta el rectángulo de la mesa y lo achica a ~512 px de lado, JPEG calidad 80.
3. **Detector de cambio** (para ahorrar llamadas): diferencia media de píxeles contra el último recorte enviado; si es menor a un umbral, no se llama (salvo que pasen >60 s, para refrescar tiempos).
4. **Claude**: una llamada con el recorte (o varias mesas juntas), el estado anterior y el prompt de abajo.
5. **Validación**: parsear con Pydantic; si falla, reintentar una vez y si no, descartar esa lectura.
6. **Envío**: `POST /api/observations` con el formato del contrato.

### Prompt (base, iterar con los videos)

```
Sos el sistema de visión de un restaurante. La imagen es una vista cenital de la mesa {table_id}.
Identificá cada plato y cada bebida que esté sobre la mesa.
Para cada plato devolvé un estado:
- "lleno": recién servido, casi intacto (más de 75 % de la comida)
- "comiendo": entre 30 % y 75 %
- "por_terminar": menos de 30 %
- "vacio": sin comida o solo restos
Para cada bebida: "llena", "media" o "vacia".
Estado anterior de esta mesa: {previous_items_json}
Mantené el mismo id para el mismo objeto (por posición). Usá ids nuevos (p3, d2…) solo para objetos nuevos.
No incluyas objetos que ya no están.
Respondé SOLO con la herramienta report_table.
```

Herramienta (JSON schema) `report_table`:

```json
{
  "items": [
    { "id": "p1", "kind": "plate", "state": "comiendo", "fill_ratio": 0.5,
      "position": "arriba-izquierda", "confidence": 0.8 }
  ],
  "people_present": true,
  "notes": "opcional, algo raro (plato volcado, mesa sin gente con platos llenos)"
}
```

`people_present` es gratis de pedir y sirve para métricas de ocupación y para no avisar "plato lleno" si la mesa está vacía.

### Suavizado

Aunque las llamadas son espaciadas, Claude puede dudar entre dos estados vecinos. Por cada item:
- Solo aceptar un cambio de estado si aparece en **2 lecturas seguidas**.
- Los estados solo avanzan (`lleno → comiendo → por_terminar → vacio`); un retroceso se ignora (si es un plato nuevo, va a tener otro id).
- Si un item falta en una lectura, mantenerlo; si falta en 2 seguidas, se considera retirado.

## Estructura sugerida

```
vision/
  main.py               # loop: captura → recorte → Claude → envío
  config/zones.json
  servio_vision/
    capture.py          # webcam / RTSP / archivo de video
    zones.py            # recortes por mesa
    change.py           # detector de cambio
    claude_client.py    # llamada a Bedrock (boto3) o API de Anthropic, tool use
    prompt.py
    smoothing.py
    client.py           # POST al backend (modo --dry-run que imprime)
  tools/
    draw_zones.py
    record.py           # grabar videos de prueba
    eval.py             # corre el prompt sobre frames etiquetados a mano y da % de acierto
  requirements.txt      # opencv-python, boto3 (o anthropic), pydantic, httpx
```

`python main.py --source videos/mesa1.mp4 --dry-run` tiene que funcionar sin backend.

## Evaluar el prompt (importante)

Sacar ~40 frames de los videos, etiquetarlos a mano (estado real de cada plato) y correr `tools/eval.py` cada vez que se cambia el prompt. Es la única forma de saber si un cambio mejoró. Probar también si mandar el frame a mayor resolución o con un modelo más grande (Sonnet) mejora lo suficiente como para pagar la diferencia.

## Futuro (roadmap, no MVP)

Si el producto escala, un detector local liviano (por ejemplo YOLO) puede filtrar frames y llamar a Claude solo cuando hace falta, para bajar costo. No es necesario para la hackathon.
