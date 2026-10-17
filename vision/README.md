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
- Cada corrida deja `out/eventos.jsonl`; se puede reproducir con `python tools/replay.py --file out/eventos.jsonl`.

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
