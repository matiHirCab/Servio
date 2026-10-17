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

## Claude

No hay créditos de AWS, así que Claude va por el Claude Code logueado (`claude -p`, suscripción del equipo). Probar con un recorte de mesa y los de sus platos:

```bash
python -m servio_vision.classifiers.claude_plates mesa.jpg plato1.jpg plato2.jpg
```

- `CLAUDE_PROVIDER=cli` (por defecto) o `anthropic` (necesita `ANTHROPIC_API_KEY` y `pip install anthropic`).
- En Windows llama a `claude.exe` directo; si no está en el PATH, poner la ruta en `CLAUDE_CLI`.
- Primera prueba (8 platos, video de IA): 6–8 s por mesa con `haiku`.

## Resultados

_(Tomás: mAP de YOLO, acierto del `hybrid`, latencia, costo y tamaño mínimo de plato; cita del dataset de Roboflow.)_
