# 04 — Visión (YOLO + Claude)

**YOLO encuentra los platos y Claude estima cuánta comida queda en cada uno.** Los dos van detrás de la interfaz `Classifier` ([03-contratos.md](03-contratos.md)), así el pipeline no depende de cuál se use.

## Por qué los dos

- **YOLO** (`yolov8n` entrenado con el dataset de Roboflow): encuentra cada plato rápido, en la laptop con CPU, y da su caja. Con la caja se mantiene el mismo id para el mismo plato entre lecturas y se recorta el plato a buena resolución aunque haya muchas mesas en el plano. Solo distingue `food` / `finished_food`, así que no alcanza para los estados intermedios.
- **Claude** (Haiku por `claude -p` con la suscripción del equipo, o la API de Anthropic si hay key): mira el recorte de cada plato y estima `fill_ratio` (cuánta comida queda), que da los 4 estados sin entrenar nada.
- **Plan B:** si Claude no responde, el `hybrid` usa la clase de YOLO (`food` → `lleno`, `finished_food` → `vacio`). Si YOLO no está entrenado a tiempo, existe `claude` con el recorte de la mesa entera, pero **manda la mesa al modelo** (lo que C-3 evita), así que es opt-in (`CLAUDE_ALLOW_TABLE_IMAGES=true`, con el detector de personas para pixelar); el prompt es `vision/prompts/claude_table.txt`. Sin pesos entrenados, `hybrid` ya anda con YOLOE.

## Clasificador `hybrid` (el de la demo)

Por cada llamada a `classify(recorte_mesa, table_id, items_anteriores)`:

1. **YOLO** detecta los platos en el recorte de la mesa → cajas + clase + confianza.
2. **Ids:** empareja cada caja con los `items_anteriores` por distancia entre centros (`servio_vision/tracking.py`). Si no hay pareja cerca, es un plato nuevo (`p{n}` siguiente).
3. **Claude:** una sola llamada por mesa con los recortes de cada plato numerados (con 10 % de margen). **La imagen de la mesa no se manda** (C-3): lo que de una persona entre en un recorte (una mano, un brazo) va pixelado, y `people_present` lo dice un detector de personas local (`people.py`, el mismo YOLO de COCO de las mesas); sin él, lo estima el modelo con los recortes. Devuelve, por plato, `fill_ratio`, `confidence`, `visible` y `same_plate`.
   - **Referencia:** cada plato manda también su recorte *recién servido*, para que Claude mida contra lo que se sirvió (sin ella, 1 galletita entera de 3 parecía «intacto»). Se toma en las primeras 3 lecturas visibles del plato y solo si Claude lo ve lleno (≥ 0,75): un id nuevo a mitad de comida no adopta un plato medio comido como «lleno». Si Claude dice `same_plate: false` (la referencia es de otra comida, p. ej. ids cambiados), se descarta y ese id no toma otra.
4. Arma el `TableReport` con `id`, `bbox`, `position` (derivada de la caja), `fill_ratio` y el estado.
5. Si Claude falla o tarda más de 10 s: estado según la clase de YOLO, con confianza más baja. Con la API caída o una clave inválida, un cortacircuito por proveedor (`classifiers/breaker.py`) deja de llamar un rato (60 s, el doble en cada prueba fallida, hasta 10 min) y se usa YOLO solo; `CLAUDE_MAX_CALLS_PER_DAY` pone un tope diario.
7. **Reinicio** (cámara en vivo, con la base): `Pipeline` guarda cada 5 s y al cerrar lo que recuerda (tabla `estado_pipeline`, junto a `estado_servicio` de los avisos): los platos de la lectura anterior, el suavizado de cada mesa (con lo que falta para confirmar un cambio), la numeración de las mesas detectadas, el plato del pedido de cada plato y, en `hybrid`, la memoria de ids (`IdCounter`), si había gente y la última lectura de cada plato. Al arrancar lo recupera si tiene menos de 10 min (`resume`); así los ids siguen siendo los mismos que los del estado de `alerts` y una lectura dudosa no cambia un estado por sí sola. **No se guardan** las referencias (un JPEG por plato) ni el conteo de lecturas: un plato que sigue lleno toma una referencia nueva y uno a medio comer sigue sin ella (el prompt lo admite). Un video o un fixture empiezan siempre de cero.
6. **Mesa sin cambios** (`CLAUDE_REUSE_UNCHANGED`, por defecto sí): no se vuelve a preguntar por una mesa que no cambió, que es la mayor parte del costo. Se reutiliza la última respuesta si los platos son los mismos, ninguno se perdió, cada uno ya tiene sus primeras lecturas (la referencia), la gente es la misma y cada recorte se ve casi igual al que se mandó (miniatura en gris, diferencia media < 5 de 255). Una lectura reutilizada **no cuenta como evidencia nueva**: los platos salen con su último estado y `visible: false`, que el suavizado ignora (así una sola respuesta, quizá equivocada, no confirma un cambio de estado). Aun así se pregunta de nuevo cada 4 lecturas, y una lectura que falló nunca se reutiliza. Los umbrales (`REUSE_MAX_DIFF`, `MAX_REUSES` en `hybrid.py`) están sin calibrar con video real: ajustarlos con el banco de pruebas.

### Prompt de Claude (iterar con el video)

El texto vigente está en `vision/prompts/claude.txt` (Claude) y `vision/prompts/openai.json` (Decisions API de OpenAI, `CLAUDE_PROVIDER=openai`); `CLAUDE_PROMPT_FILE` / `OPENAI_PROMPT_FILE` del `.env` apuntan a otro archivo sin tocar el default. En resumen, por plato:

- `fill_ratio`: fracción de la porción servida que queda, de 0 (vacío o restos) a 1 (intacto). Con referencia, la referencia vale 1 y se cuentan las piezas.
- `confidence`: de 0 a 1.
- `visible`: false **solo** si una mano, un brazo o un cubierto agarrado está encima de la comida o agarrando comida. Cubiertos apoyados (también cruzados encima del plato), servilletas o una mano en el borde no cuentan: se estima con lo que se ve. Un plato con los cubiertos apoyados y sin comida a la vista está vacío.
- `same_plate`: con referencia, false si la referencia muestra otra comida; sin referencia, true.

Y por mesa, `people_present`.

Respuesta `report_plates` (esquema `REPORT_PLATES_SCHEMA`, todos los campos obligatorios):

```json
{ "plates": [ { "index": 1, "fill_ratio": 0.5, "confidence": 0.8, "visible": true, "same_plate": true } ],
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
frame (cada 30 s) → caras difuminadas → recorte por mesa → classify() → suavizado → observación → alerts
```

0. **Privacidad** (`servio_vision/privacy.py`, C-3): apenas se lee el frame, en `capture.py`, un detector de caras local (YuNet, el de OpenCV; el modelo se baja una vez a `vision/models/`) pixela cada cara. Es el único lugar: la vista de demo, `probar_video`, `draw_zones`, las grabaciones (`main.py --record DIR`) y cualquier imagen de `out/` ya no tienen caras. El detector corre a 1280 px de ancho (desde una cámara en alto las caras miden 10–20 px y por debajo no las ve), con umbral bajo, y solo descarta cajas más altas que la mitad del frame (un torso o una mesa, no una cara: taparía platos); ante la duda se pixela de más. El enganche es uno solo: `open_source` envuelve el iterador de frames crudos de cada lector; en la cámara se difumina solo el frame de cada lectura, no los ~30 fps que el hilo de `CameraReader` descarta. En la vista en vivo de un video detecta cada 3 frames y reusa las cajas para mantener los fps (en una carpeta de fotos, en todas: son imágenes distintas). Sin el modelo y sin internet, el programa no arranca. **No se guarda ningún frame salvo con `--record`**, y esos también salen difuminados. La otra mitad de C-3 está en `hybrid.py`/`people.py`: al modelo van solo recortes de plato, con las personas pixeladas.
1. **Mesas**: por defecto se **detectan solas** en cada lectura (`servio_vision/tables.py`): un YOLO de COCO (clase "dining table") encuentra la mesa y `TableTracker` le da un nombre estable ("Mesa 1") aunque se mueva; si se movió, las cajas de los platos se pasan al recorte nuevo. Si en una lectura no se ve, queda donde estaba; si nunca se vio ninguna, se usa el cuadro entero. Con `--zones config/zones.json` se usan zonas fijas (dibujadas con `tools/draw_zones.py`).
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
- Las cantidades de lecturas y la confianza se cambian en el `.env`: `SMOOTH_READINGS` (2), `SMOOTH_HARD_READINGS` (3) y `SMOOTH_MIN_CONFIDENCE` (0.8). Con la Decisions API de OpenAI conviene bajar la confianza (~0.6): da confianzas más bajas que Claude y, con 0.8, un salto como `lleno → por_terminar` puede no aceptarse nunca.
- Plato terminado por tiempo: si queda en `por_terminar` durante `--finished-after` segundos de video (o `FINISHED_AFTER` en el `.env`; por defecto 6 min, y en demo 6 lecturas), pasa a `vacio` (son restos). Las lecturas `por_terminar` posteriores no lo devuelven atrás; solo un plato que se vuelve a servir sale de `vacio`.

## Estructura

```
vision/
  main.py                    # flags → Settings → app.run (Jessica)
  config/servio.example.toml # modelo del toml de cada casa (Jessica)
  config/zones.json          # (Jessica)
  servio_vision/
    schemas.py, classifier.py   # contrato (Tomás; cambios con revisión de Jessica)
    classifiers/
      fake.py                # (Jessica)
      yolo.py, hybrid.py, claude.py   # (Tomás)
    tracking.py              # emparejar cajas con ids anteriores (Tomás)
    prompt.py                # (Tomás)
    settings.py, cli.py        # configuración en un solo lugar y flags (Jessica)
    app.py, pipeline.py        # armado del pipeline y el pipeline (Jessica)
    capture.py, zones.py, smoothing.py, privacy.py, recording.py   # (Jessica)
    alerts/                  # estado de mesa, reglas, Telegram, log (Matías)
  tools/
    draw_zones.py            # (Jessica)
    build_eval_set.py, eval.py, train_yolo.ipynb   # (Tomás)
    replay.py                # (Matías)
    vista_demo.py            # vista de la demo (Jessica)
    resumen.py               # tabla de lo que pasó, si sobra tiempo (Jessica)
  tests/
  data/ models/ out/         # ignorados por git
  requirements.txt           # opencv-python, pydantic, httpx (anthropic u openai solo si CLAUDE_PROVIDER lo pide)
  requirements-yolo.txt      # ultralytics
```

`python main.py --source data/videos/demo.mp4 --classifier fake --no-telegram` tiene que andar sin modelo ni internet.

## Evaluar

- `tools/eval.py --set data/eval/test --classifier hybrid`: acierto de estado en recortes de plato del test set (`food` → `lleno`, `finished_food` → `vacio`), latencia y costo. Sirve para ajustar el prompt, no para elegir modelo.
- Sobre el video de la demo: revisar la línea de tiempo de estados de cada mesa en `out/eventos.jsonl` y que los ids no salten.
