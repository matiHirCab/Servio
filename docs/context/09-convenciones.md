# 09 — Convenciones

## Estructura del repo

```
/
  AGENTS.md            # índice para agentes (leer primero)
  README.md
  .env.example         # todas las variables, sin valores
  docs/context/        # contexto del proyecto
  fixtures/            # observaciones de ejemplo y avisos esperados (ver 03-contratos.md)
  vision/              # todo el programa en Python (ver 04-vision.md)
  .github/workflows/   # CI
```

## Dueños por carpeta

"Trabajá solo en tus carpetas" (regla 2 de `AGENTS.md`) significa: solo en las de esta tabla. Para tocar la de otro, avisale y que revise el PR.

| Carpeta | Dueño |
|---|---|
| `vision/servio_vision/classifiers/` (salvo `fake.py`), `tracking.py`, `prompt.py`, `tools/{build_eval_set,eval}.py`, `tools/train_yolo.ipynb` | Tomás |
| `vision/servio_vision/schemas.py`, `classifier.py` | Tomás (contrato con Jessica: cambios con su revisión) |
| `vision/servio_vision/alerts/`, `tools/replay.py`, `fixtures/` | Matías |
| `vision/main.py`, `servio_vision/{capture,zones,smoothing}.py`, `classifiers/fake.py`, `tools/draw_zones.py`, `tools/vista_demo.py`, `config/` | Jessica |
| `docs/` | Todos, por PR |

## Código

- Docs y comentarios en español; identificadores en inglés.
- Python 3.11, `venv` + `requirements.txt`, `ruff format` y `ruff check`, tests con `pytest`.
- Las dependencias de YOLO (`ultralytics`, torch) van en `requirements-yolo.txt`, aparte, para que el CI no las instale.
- Configuración por `.env`; los flags de CLI le ganan a las variables (`--demo` le gana a `DEMO_MODE`).
- Los valores de enums son exactamente los de [03-contratos.md](03-contratos.md) (`lleno`, `comiendo`, `por_terminar`, `vacio`…). No traducir ni renombrar.

## Git

- Ramas: `tomas/<tema>`, `matias/<tema>`, `jessica/<tema>`.
- Commits cortos en presente: `vision: agrega suavizado por voto mayoritario`.
- PR chico a `main`, otra persona aprueba. `main` siempre tiene que andar.

## Variables de entorno

Ver `.env.example`: `AWS_REGION`, `AWS_PROFILE`, `BEDROCK_MODEL_ID`, `CLAUDE_PROVIDER`, `ANTHROPIC_API_KEY`, `YOLO_WEIGHTS`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_GROUP_CHAT_ID`, `DEMO_MODE`.

## Privacidad y datos

- A Claude solo se mandan recortes de mesas y platos, no el salón entero.
- Datasets, pesos e imágenes nunca se suben al repo (`vision/data/`, `vision/models/` y `vision/out/` están en `.gitignore`):
  - Video de la demo grabado por el equipo: carpeta compartida del equipo.
  - Dataset *AI Smart Restaurant Surveillance* de Roboflow (CC BY 4.0): se baja en local para entrenar y evaluar; se cita en `vision/README.md`.
  - Videos de YouTube: solo para probar, no se muestran en la demo ni se redistribuyen.
