# vision/

Captura de cámara, recorte por mesa y lectura de estado con Claude multimodal. Contexto completo: [docs/context/04-vision.md](../docs/context/04-vision.md). Contratos: [docs/context/03-contratos.md](../docs/context/03-contratos.md).

Estado: **todavía sin código** (se crea en la hora 1–6 del plan).

## Arranque esperado (con mocks, sin backend)

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py --source videos/mesa1.mp4 --dry-run
```

`--dry-run` imprime el JSON del contrato en lugar de hacer `POST /api/observations`. Con `DEMO_MODE=true` se usan las cadencias cortas de la tabla de `04-vision.md`.
