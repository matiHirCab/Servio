# vision/

Todo Servio en Python: captura, recorte por mesa, clasificación de platos (YOLO + Claude detrás de la interfaz `Classifier`), suavizado, reglas de avisos y envío a Telegram. Contexto: [04-vision.md](../docs/context/04-vision.md), [05-reglas-alertas.md](../docs/context/05-reglas-alertas.md). Contratos: [03-contratos.md](../docs/context/03-contratos.md).

## Arranque

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-yolo.txt          # para --classifier yolo o hybrid
python main.py --source data/videos/demo.mp4 --classifier fake --no-telegram
```

- `--classifier fake|yolo|hybrid|claude`: `fake` no usa modelo; `hybrid` (YOLO + Claude) es el de la demo; `yolo` es el plan B sin internet.
- `--demo` (o `DEMO_MODE=true`): umbrales de demo (la cadencia es siempre `--interval`, 30 s). El flag le gana a la variable.
- `--no-telegram`: imprime los avisos en vez de mandarlos.
- Cada corrida deja `out/eventos.jsonl`; se puede reproducir (plan B de la demo) con `python tools/replay.py --file out/eventos.jsonl --speed 10` (`--no-telegram` para imprimir). Como el log se agrega, el replay lo corta en corridas (`run_started`) y reproduce la última con los umbrales con que se hizo (`--run N` para otra, `--all` para todas). También acepta un fixture: `--file ../fixtures/observations/mesa_completa.jsonl`, con umbrales de producción (`--demo` para los de demo). Escribe su propio log en `out/replay.jsonl` (se pisa en cada replay).

## Vista de la demo (pantalla dividida)

- **Izquierda:** `tools/vista_demo.py`, con los mismos flags que `main.py`. Muestra el video con cada mesa coloreada por estado y la caja de cada plato.
- **Derecha:** el celular con el grupo de Telegram, espejado con [`scrcpy`](https://github.com/Genymobile/scrcpy) (Android).

```bash
python tools/vista_demo.py --source data/videos/demo.mp4 --classifier hybrid --demo --interval 15
python tools/vista_demo.py --source data/videos/demo.mp4 --classifier fake --record out/vista.mp4   # grabar (plan B)
```

- **Teclas:** `q` sale, espacio pausa. Con `--no-window --record` graba sin abrir la ventana.
- **`--speed`:** a diferencia de `main.py`, sin `--speed` la vista reproduce en tiempo real (es para mirar). Después de una pausa o de esperar al modelo, el video retoma su ritmo sin adelantarse.
- **Grabar en vivo:** con la cámara, la grabación puede quedar más rápida que la realidad si el loop va más lento que los fps de la cámara. Para el plan B, grabar sobre el video.
- **Colores de mesa:** gris libre · azul servida · verde comiendo · amarillo por terminar · rojo terminada. Los platos usan los mismos colores según su estado.

## Claude

No hay créditos de AWS, así que Claude va por el Claude Code logueado (`claude -p`, suscripción del equipo). Probar con un recorte de mesa y los de sus platos:

```bash
python -m servio_vision.classifiers.claude_plates mesa.jpg plato1.jpg plato2.jpg
```

- `CLAUDE_PROVIDER=cli` (por defecto) o `anthropic` (necesita `ANTHROPIC_API_KEY` y `pip install anthropic`).
- En Windows llama a `claude.exe` directo; si no está en el PATH, poner la ruta en `CLAUDE_CLI`.
- Primera prueba (8 platos, video de IA): 6–8 s por mesa con `haiku`.

## YOLO + Claude (`hybrid`)

`--classifier hybrid` es el de la demo: YOLO encuentra los platos, se mantienen los ids por posición y Claude estima cuánto queda en cada uno.

- Sin `YOLO_WEIGHTS` usa YOLOE preentrenado con los prompts `plate` y `bowl` (`imgsz=1280`, lo que funcionó en las pruebas: 11/11 platos en el video de IA). Encuentra los platos pero no su estado, así que sin Claude la lectura se descarta.
- Con `YOLO_WEIGHTS` (el `yolov8n` entrenado con `food` / `finished_food`), si Claude falla usa el estado de YOLO con confianza más baja. Es también lo que usa `--classifier yolo`.
- Probar sobre un video (un frame por segundo, el frame entero como una mesa):

```bash
pip install -r requirements.txt -r requirements-yolo.txt
python tools/probar_video.py "C:/Users/tomas/Downloads/video mesa platos ia.mp4" --max-frames 5
```

Cada corrida va a su carpeta (`out/video/run1`, `run2`, ...) con cada frame dibujado (`f_NNN.jpg`), la hoja con el recorte de cada plato y su clasificación (`f_NNN_platos.jpg`) y `linea_de_tiempo.json`.

Para medir qué tan estables son los ids (TOM: estabilidad de ids), solo con YOLO y sin gastar Claude:

```
python tools/id_stability.py "C:/Users/tomas/Downloads/video mesa platos ia.mp4" --cada 0.5
python tools/id_stability.py out/video/run1/linea_de_tiempo.json
```

Imprime los ids de cada frame y `conservan`: qué fracción de los platos de un frame sigue con el mismo id en el siguiente. Un plato que YOLO deja de ver hasta 3 lecturas recupera su id si reaparece en su lugar (`MAX_MISSING` en `tracking.py`).

## Resultados

_(Tomás: mAP de YOLO, acierto del `hybrid`, latencia, costo y tamaño mínimo de plato; cita del dataset de Roboflow.)_

## Evaluación de estado (TOM-6)

```
python tools/eval.py --set data/eval
```

`data/eval/` tiene `labels.csv` (`archivo,estado,nota`) y las imágenes de cada plato; no va al repo. El set actual está en la carpeta compartida del proyecto (`servio-eval/`): 119 platos, 110 `lleno` (recortes del video de IA y platos de más de 300 g de Nutrition5k), 8 `por_terminar` y 1 `vacio` (Nutrition5k, etiquetados a ojo). Falta sumar platos a medio comer y vacíos (dataset de Roboflow).

Manda los platos de a 8 por llamada, como una mesa. Resultado con `claude -p` y Haiku (2026-10-17):

| Prompt | Acierto | A un estado | `fill_ratio` medio de los llenos | Latencia por llamada |
|---|---|---|---|---|
| Original | 19,5 % | 100 % | 0,67 | 6,9 s |
| Porción servida + manos/cubiertos no cuentan | 67–69 % | 98–99 % | 0,83–0,84 | 6,6 s |

Casi todos los errores que quedan son platos llenos que Claude puntúa 0,8 justo en el corte (`lleno` es > 0,80) o 0,5–0,7 con brazos encima.
