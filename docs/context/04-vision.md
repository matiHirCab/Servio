# 04 — Visión (Claude por defecto, YOLO a prueba)

La clasificación va detrás de una interfaz única, `Classifier` (ver [03-contratos.md](03-contratos.md)). La implementación **por defecto es Claude multimodal** vía AWS Bedrock (créditos) o la API de Anthropic. En paralelo se evalúa un **YOLO entrenado** con el mismo test set; solo reemplaza o complementa a Claude si gana antes de la hora 12 (ver «Decisión YOLO vs. Claude»). La parte en Python que captura, recorta, suaviza y envía (P3) no depende de cuál se use.

## Por qué

- **Claude por defecto:** cero entrenamiento; entiende "plato a medio comer" sin que le enseñemos y da los 4 estados (`lleno`, `comiendo`, `por_terminar`, `vacio`). En 2 horas hay clasificación funcionando.
- **YOLO a prueba:** un YOLO preentrenado (COCO) no sirve tal cual (no tiene "plato" ni sabe si está lleno), pero entrenado con el dataset de Roboflow (`food` / `finished_food`) puede ser más rápido y barato. Vale la pena tener el dato real para decidir y para el pitch.
- **Interfaz única:** como los dos devuelven un `TableReport`, cambiar de uno a otro es un flag (`--classifier`), no un cambio de pipeline.
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

**Pendiente día 1:** reemplazar la cuenta de abajo por el número real. Cuenta rápida de costo: con 4 mesas, una llamada cada 20 s solo si hubo cambio, son unas pocas miles de llamadas por día de servicio. Calcular el número real con los precios vigentes de Bedrock para el modelo elegido el día 1 y anotarlo acá; para la hackathon los créditos de AWS sobran.

## Cadencias (una sola tabla)

| Qué | Producción | Modo demo |
|---|---|---|
| Captura de frame | 1 s | 1 s |
| Llamada a Claude por mesa (si el recorte cambió) | 15–30 s | 5–10 s |
| Refresco forzado sin cambios | 60 s | 20 s |
| Observación al backend | tras cada lectura | tras cada lectura |

Los umbrales de [05-reglas-alertas.md](05-reglas-alertas.md) deben ser ≥ 2 × la cadencia de llamada a Claude.

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
Identificá cada plato de comida que esté sobre la mesa. Ignorá vasos, tazas, botellas y cubiertos.
Para cada plato devolvé un estado:
- "lleno": recién servido, casi intacto (más de 80 % de la comida)
- "comiendo": entre 30 % y 80 %
- "por_terminar": entre 5 % y 30 %
- "vacio": menos de 5 % (sin comida o solo restos)
Devolvé también fill_ratio (0 a 1): la fracción de comida que queda, coherente con el estado.
Estado anterior de esta mesa: {previous_items_json}
Si una mano, un brazo o una cabeza tapa un plato y no podés ver cuánta comida queda, devolvelo igual con "visible": false.
Mantené el mismo id para el mismo objeto (por posición). Usá ids nuevos (p3, p4…) solo para objetos nuevos.
No incluyas objetos que ya no están.
Respondé SOLO con la herramienta report_table.
```

Herramienta (JSON schema) `report_table`:

```json
{
  "items": [
    { "id": "p1", "kind": "plate", "state": "comiendo", "fill_ratio": 0.5,
      "position": "arriba-izquierda", "confidence": 0.8, "visible": true }
  ],
  "people_present": true,
  "notes": "opcional, algo raro (plato volcado, mesa sin gente con platos llenos)"
}
```

`visible`: `false` si algo tapa el plato. Esos ítems **no cuentan como lectura** en el suavizado (ni para cambiar de estado ni como ausencia) y no se mandan al backend.

`people_present` es gratis de pedir y sirve para métricas de ocupación y para no avisar "plato lleno" si la mesa está vacía.

### Suavizado

Aunque las llamadas son espaciadas, Claude puede dudar entre dos estados vecinos. Por cada item:
- Solo aceptar un cambio de estado si aparece en **2 lecturas seguidas**.
- Los estados avanzan (`lleno → comiendo → por_terminar → vacio`). Un **retroceso** (por ejemplo `vacio → comiendo`) y un **salto de más de un estado** (por ejemplo `lleno → vacio`) se aceptan solo si aparecen en **3 lecturas seguidas** con `confidence ≥ 0.8`. Así un error de Claude no deja un plato atascado en `vacio`, y un plato que el cliente vuelve a llenar o que sigue en consumo se corrige solo.
- Si un item falta en una lectura, mantenerlo; si falta en 2 seguidas, se considera retirado.
- Un item con `visible: false` se ignora en esa lectura: se mantiene su último estado suavizado.

## Estructura sugerida

Dueños por carpeta en [09-convenciones.md](09-convenciones.md).

```
vision/
  main.py               # loop: captura → recorte → classify() → suavizado → envío   (P3)
  config/zones.json     # (P3)
  servio_vision/
    schemas.py          # Pydantic de report_table: Item, TableReport               (P1, contrato)
    classifier.py       # Protocol Classifier + get_classifier(name)                (P1, contrato)
    classifiers/
      fake.py           # sin modelo: script por tiempo o replay de fixtures        (P3)
      claude.py         # Bedrock (boto3) o API de Anthropic, tool use              (P1)
      yolo.py           # solo si se decide usar YOLO                               (P1)
    prompt.py           # (P1)
    capture.py          # carpeta / video / webcam / URL                            (P3)
    zones.py            # recortes por mesa                                         (P3)
    change.py           # detector de cambio                                        (P3)
    smoothing.py        # (P3)
    client.py           # POST al backend; --dry-run escribe .jsonl                 (P3)
  tools/
    draw_zones.py       # (P3)
    build_eval_set.py   # recortes y labels desde el dataset de Roboflow            (P1)
    eval.py             # acierto de estado sobre un set etiquetado                 (P1)
    id_stability.py     # estabilidad de ids sobre un video, sin el pipeline        (P1)
    yolo_baseline.ipynb # entrenamiento de YOLO en Colab con GPU                    (P1)
  tests/                # pytest
  data/                 # ignorado: roboflow/, eval/, videos/
  models/               # ignorado: pesos de YOLO
  out/                  # ignorado: crops/, obs.jsonl
  requirements.txt      # opencv-python, boto3, anthropic, pydantic, httpx
  requirements-yolo.txt # ultralytics (aparte, para que el CI no instale torch)
```

`python main.py --source data/videos/mesa1.mp4 --classifier fake --dry-run` tiene que funcionar sin backend ni modelo.

## Evaluar el clasificador (importante)

Es la única forma de saber si un cambio mejoró. Hay dos fuentes de datos:

- **Fotos del dataset de Roboflow** (*AI Smart Restaurant Surveillance*, CC BY 4.0): `food` / `finished_food` ya etiquetados. Se separa un **test split** (15 %) que no se usa para iterar el prompt ni para entrenar YOLO; el resto (`dev`) es para iterar. Sirve para lleno vs. vacío, no para estados intermedios.
- **Videos cenitales** (YouTube para probar, y el video de nuestra mesa para el ajuste fino): para estabilidad de ids y la línea de tiempo de estados.

`tools/eval.py` y `tools/id_stability.py` reportan:

- **% de acierto de estado** por plato.
- **Estabilidad de ids**: sobre secuencias de frames consecutivos del mismo video, % de objetos que conservan su id entre lecturas. Si es baja, la reconciliación del backend (ver [03-contratos.md](03-contratos.md)) trabaja de más y los tiempos de las alertas se vuelven poco confiables.
 Probar también si mandar el frame a mayor resolución o con un modelo más grande (Sonnet) mejora lo suficiente como para pagar la diferencia.

## Decisión YOLO vs. Claude

Tarea P1-7 de [11-tareas.md](11-tareas.md), con tiempo límite de 1 h 30. Se entrena `yolov8n` en Colab con GPU sobre `dev` y se compara contra Claude en el mismo test split. Tabla de resultados en `vision/README.md`: acierto, latencia por imagen, costo por 1.000 imágenes, tiempo de armado y si da estados intermedios.

| Resultado | Qué hacemos |
|---|---|
| Claude ≥ YOLO, o diferencia chica | Seguimos solo con Claude. |
| YOLO claramente mejor y **antes de la hora 12** | `classifiers/yolo.py` detrás de `Classifier` (o híbrido: YOLO detecta y da lleno/vacío, Claude solo para estados intermedios). Inferencia de `yolov8n` en la laptop con CPU. |
| YOLO claramente mejor, pero **después de la hora 12** | Seguimos con Claude; el híbrido queda como roadmap en el pitch. |

**Decisión tomada:** _(completar en la hora 12)_

## Futuro (roadmap, no MVP)

Si el producto escala, un detector local liviano (YOLO entrenado con datos de restaurantes reales) puede filtrar frames y llamar a Claude solo cuando hace falta, para bajar costo por mesa.
