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
- `--demo` (o `DEMO_MODE=true`): cadencias y umbrales cortos. El flag le gana a la variable.
- `--no-telegram`: imprime los avisos en vez de mandarlos.
- Cada corrida deja `out/eventos.jsonl`; se puede reproducir con `python tools/replay.py --file out/eventos.jsonl`.

## Resultados

_(Tomás: mAP de YOLO, acierto del `hybrid`, latencia, costo y tamaño mínimo de plato; cita del dataset de Roboflow.)_
