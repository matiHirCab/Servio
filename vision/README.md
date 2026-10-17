# vision/

Captura de cámara, recorte por mesa y lectura de estado detrás de la interfaz `Classifier` (Claude por defecto; `fake` para trabajar sin modelo; YOLO solo si gana la comparativa). Contexto completo: [docs/context/04-vision.md](../docs/context/04-vision.md). Contratos: [docs/context/03-contratos.md](../docs/context/03-contratos.md).

Estado: solo el contrato en código (`servio_vision/contracts.py`) con sus tests contra [`fixtures/`](../fixtures/README.md). El resto se crea en la hora 1–6 del plan.

## Arranque esperado (con mocks, sin backend)

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # requirements.txt + pytest y ruff
python main.py --source data/videos/mesa1.mp4 --classifier fake --dry-run --out out/obs.jsonl
```

`--dry-run` escribe las observaciones en `.jsonl` (formato de `fixtures/observations/`) en lugar de hacer `POST /api/observations`. Con `DEMO_MODE=true` o `--demo` (el flag le gana a la variable) se usan las cadencias cortas de la tabla de `04-vision.md`.

Para YOLO (solo si se decidió usarlo): `pip install -r requirements-yolo.txt`.
