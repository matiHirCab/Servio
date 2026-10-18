# 04 — Visión (YOLO + Claude)

**YOLO encuentra los platos y Claude estima cuánta comida queda en cada uno.** Los dos van detrás de la interfaz `Classifier` ([03-contratos.md](03-contratos.md)), así el pipeline no depende de cuál se use.

## Por qué los dos

- **YOLO** (`yolov8n` entrenado con el dataset de Roboflow): encuentra cada plato rápido, en la laptop con CPU, y da su caja. Con la caja se mantiene el mismo id para el mismo plato entre lecturas y se recorta el plato a buena resolución aunque haya muchas mesas en el plano. Solo distingue `food` / `finished_food`, así que no alcanza para los estados intermedios.
- **Claude** (Haiku por `claude -p` con la suscripción del equipo, o la API de Anthropic si hay key): mira el recorte de cada plato y estima `fill_ratio` (cuánta comida queda), que da los 4 estados sin entrenar nada.
- **Plan B:** si Claude no responde, el `hybrid` usa la clase de YOLO (`food` → `lleno`, `finished_food` → `vacio`). Si YOLO no está entrenado a tiempo, se usa `claude` con el recorte de la mesa entera.

## Clasificador `hybrid` (el de la demo)

Por cada llamada a `classify(recorte_mesa, table_id, items_anteriores)`:

1. **YOLO** detecta los platos en el recorte de la mesa → cajas + clase + confianza.
2. **Ids:** empareja cada caja con los `items_anteriores` por distancia entre centros (`servio_vision/tracking.py`). Si no hay pareja cerca, es un plato nuevo (`p{n}` siguiente).
3. **Claude:** una sola llamada por mesa con el recorte de la mesa (para `people_present`) y los recortes de cada plato numerados (con 10 % de margen). Devuelve, por plato, `fill_ratio`, `confidence`, `visible` y `same_plate`.
   - **Referencia:** cada plato manda también su recorte *recién servido*, para que Claude mida contra lo que se sirvió (sin ella, 1 galletita entera de 3 parecía «intacto»). Se toma en las primeras 3 lecturas visibles del plato y solo si Claude lo ve lleno (≥ 0,75): un id nuevo a mitad de comida no adopta un plato medio comido como «lleno». Si Claude dice `same_plate: false` (la referencia es de otra comida, p. ej. ids cambiados), se descarta y ese id no toma otra.
   - **La comida no vuelve:** una lectura que sube más de 0,2 respecto de la anterior no cuenta (queda la anterior con `visible: false`): casi siempre es una mano que Claude no marcó. Si sube 3 lecturas seguidas se acepta, porque es otro plato con el mismo id.
4. Arma el `TableReport` con `id`, `bbox`, `position` (derivada de la caja), `fill_ratio` y el estado.
5. Si Claude falla o tarda más de 10 s: estado según la clase de YOLO, con confianza más baja.

### Prompt de Claude (base, iterar con el video)

```
Sos el sistema de visión de un restaurante. La primera imagen es una vista desde arriba de la mesa {table_id}.
Las siguientes son recortes de cada plato de esa mesa, numerados desde 1 en el mismo orden.
Para cada plato estimá:
- fill_ratio: cuánta comida queda, de 0 (vacío o solo restos) a 1 (recién servido, intacto).
- confidence: de 0 a 1.
- visible: false solo si no se ve la comida del plato (una persona o un brazo tapa más de la mitad).
  Cubiertos, servilletas o una mano en el borde no cuentan: estimá igual con lo que se ve.
Indicá también si hay personas sentadas en la mesa (people_present).
Respondé SOLO con la herramienta report_plates.
```

Herramienta `report_plates`:

```json
{ "plates": [ { "index": 1, "fill_ratio": 0.5, "confidence": 0.8, "visible": true } ],
  "people_present": true }
```

`temperature: 0`, herramienta forzada, validación con Pydantic y un reintento.

## Entrenar YOLO

- Dataset: *AI Smart Restaurant Surveillance* de Roboflow (CC BY 4.0), versión **a color y sin augmentación**, formato YOLO, en `vision/data/roboflow/` (ignorado por git). Clases que nos importan: `food` y `finished_food`.
- Separar un **test set** del 15 % que no se usa para entrenar ni para iterar el prompt.
- `vision/tools/train_yolo.ipynb` en **Colab con GPU**: `yolov8n`, ~30 épocas, imagen 640. Pesos a `vision/models/best.pt` (ignorado por git; se comparten por la carpeta del equipo). Ruta en `YOLO_WEIGHTS`.
- Dependencias de YOLO (`ultralytics`) en `vision/requirements-yolo.txt`, aparte, para que el CI no instale torch.

## Trade-offs

| Riesgo | Mitigación |
|---|---|
| **Muchas mesas en un plano** → platos chicos | Recortar desde el frame original (no achicado); YOLO recorta cada plato antes de mandarlo a Claude. Probar temprano con el video de la demo qué tamaño mínimo de plato funciona. |
| **Latencia de Claude** (1–3 s por llamada) | Miramos cada 30 s y las mesas se clasifican en paralelo: sobra tiempo para todas antes de la próxima lectura. |
| **Costo** | Una llamada por mesa cada 30 s; recortes chicos; Haiku. |
| **Límites de uso de la suscripción** (`claude -p`) | Una llamada por mesa cada 30 s; usar la cuenta Max para la demo; API de Anthropic como respaldo. |
| **Sin internet** | `--classifier yolo` (solo YOLO) o reproducir un log con `tools/replay.py`. |
| **Manos que tapan** | Una mano, brazo o cubierto agarrado encima de la comida inflaba el `fill_ratio`. Claude marca `visible: false` en ese caso (una mano en el borde no cuenta); el plato conserva su última lectura con `visible: false` (`hybrid`) y no cuenta en el suavizado ni en la votación. |

## Cadencias

Una sola cadencia: **se mira cada 30 s** (`--interval 30`, configurable). En cada lectura se clasifican todas las mesas y sale una observación por mesa a `alerts`, haya cambiado o no. Sin detector de cambio: a esta cadencia no hace falta ahorrar llamadas.

- En un **video**, son 30 s *del video*. `--speed` lo reproduce más rápido en el reloj (`--speed 10`: una lectura cada 3 s), pero **no acorta la demo**: las reglas cuentan tiempo de video. Con un video corto, lo que sirve es bajar `--interval` (por ejemplo 15), que baja también los umbrales de demo (ver «Umbrales configurables» en [05-reglas-alertas.md](05-reglas-alertas.md)).
- Como el suavizado pide **2 lecturas seguidas** para aceptar un cambio, un cambio de estado se confirma recién a los **60 s**. Todo umbral de [05-reglas-alertas.md](05-reglas-alertas.md) (también los de demo) debe ser ≥ 2 × `interval`.
- `--demo` (o `DEMO_MODE=true`, el flag le gana) solo cambia los umbrales de las reglas, no la cadencia.

## Video y cámara

- Vista **desde arriba** (cenital o en alto), varias mesas, 1080p o más, luz pareja.
- Fuente: archivo de video (`--source demo.mp4`, el ts es el tiempo del video), carpeta de fotos o cámara del celular como IP (IP Webcam / DroidCam).
- Videos de YouTube: solo para probar, no para mostrar.

## Pipeline

```
frame (cada 30 s) → recorte por mesa → classify() → suavizado → observación → alerts
```

1. **Zonas**: polígono por mesa en `config/zones.json`, dibujado con `tools/draw_zones.py`.
2. **Recorte**: el rectángulo de la mesa desde el frame original, JPEG calidad 85.
3. **Clasificador**: `--classifier fake|yolo|hybrid|claude`.
4. **Suavizado** (abajo).
5. **Observación** a `alerts.process()` y al log.

### Suavizado

Por cada plato:
- Un plato con `visible: false` no cuenta como lectura. Si ya se había visto, el `hybrid` lo manda con su última lectura y `visible: false`: se sigue mostrando su último estado pero no suma a la racha. Recién con 3 lecturas seguidas sin verlo se muestra «tapado» (sin estado).
- Un plato que YOLO deja de encontrar se le sigue mandando a Claude con su última caja, hasta 3 lecturas. Si Claude lo ve, sigue con su id; si no, se muestra su última lectura y después deja de venir (no queda «tapado»: lo más probable es que se lo hayan llevado). Si Claude lo lee vacío o bajando más de 0,3 de golpe, se toma como no visto, porque puede estar mirando el hueco donde estaba el plato.
- Cambio a un estado vecino: se acepta si aparece en **2 lecturas seguidas**.
- Retroceso (`vacio → comiendo`) o salto de más de un estado (`lleno → vacio`): se acepta con **3 lecturas seguidas** con `confidence ≥ 0.8`.
- Ausente en 1 lectura: se mantiene. Ausente en 2 seguidas: retirado (deja de venir en la observación).

## Estructura

```
vision/
  main.py                    # loop (Jessica)
  config/zones.json          # (Jessica)
  servio_vision/
    schemas.py, classifier.py   # contrato (Tomás; cambios con revisión de Jessica)
    classifiers/
      fake.py                # (Jessica)
      yolo.py, hybrid.py, claude.py   # (Tomás)
    tracking.py              # emparejar cajas con ids anteriores (Tomás)
    prompt.py                # (Tomás)
    capture.py, zones.py, smoothing.py   # (Jessica)
    alerts/                  # estado de mesa, reglas, Telegram, log (Matías)
  tools/
    draw_zones.py            # (Jessica)
    build_eval_set.py, eval.py, train_yolo.ipynb   # (Tomás)
    replay.py                # (Matías)
    vista_demo.py            # vista de la demo (Jessica)
    resumen.py               # tabla de lo que pasó, si sobra tiempo (Jessica)
  tests/
  data/ models/ out/         # ignorados por git
  requirements.txt           # opencv-python, pydantic, httpx (anthropic solo si CLAUDE_PROVIDER=anthropic)
  requirements-yolo.txt      # ultralytics
```

`python main.py --source data/videos/demo.mp4 --classifier fake --no-telegram` tiene que andar sin modelo ni internet.

## Evaluar

- `tools/eval.py --set data/eval/test --classifier hybrid`: acierto de estado en recortes de plato del test set (`food` → `lleno`, `finished_food` → `vacio`), latencia y costo. Sirve para ajustar el prompt, no para elegir modelo.
- Sobre el video de la demo: revisar la línea de tiempo de estados de cada mesa en `out/eventos.jsonl` y que los ids no salten.
