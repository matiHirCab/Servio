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
| `vision/main.py`, `servio_vision/{settings,cli,app,pipeline,capture,privacy,recording,zones,smoothing,tables}.py`, `classifiers/fake.py`, `tools/draw_zones.py`, `tools/{vista_demo,resumen}.py`, `config/` | Jessica |
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

## Configuración

**Un solo lugar: `servio_vision/settings.py`** (A-1). Es el único módulo que lee el entorno; `grep -r getenv servio_vision` no tiene que encontrar otro. Un `Settings` junta, en este orden de prioridad, los **flags** (`servio_vision/cli.py`), el **`servio.toml` de la casa** (`--config`, modelo en `vision/config/servio.example.toml`), el **entorno y el `.env`** del repo, y los defaults. `app.py` arma el pipeline con ese objeto y pasa cada valor por parámetro; un módulo que se construye sin parámetros (herramientas, tests) toma `settings.current()`.

- Si agregás algo configurable: un campo en `Settings` (con su rango), una línea en `.env.example` y, si es de la casa, en `servio.example.toml`. Nada de `os.getenv` en otro archivo.
- Variables del `.env` (ver `.env.example`): `CLAUDE_PROVIDER`, `CLAUDE_MODEL`, `OPENAI_MODEL`, `CLAUDE_TIMEOUT_S`, `OPENAI_TIMEOUT_S`, `CLAUDE_CLI`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `CLAUDE_PROMPT_FILE`, `OPENAI_PROMPT_FILE`, `SMOOTH_READINGS`, `SMOOTH_HARD_READINGS`, `SMOOTH_MIN_CONFIDENCE`, `YOLO_WEIGHTS`, `YOLO_DEVICE`, `FACE_MODEL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_GROUP_CHAT_ID`, `DEMO_MODE`, `ALERT_T1`, `ALERT_T2`, `ALERT_REMINDER`, `FINISHED_AFTER`, `DATABASE_URL`. Las claves de la casa (`source`, `interval`, `classifier`, `zones`…) van en el toml o en flags; en el entorno llevan prefijo (`SERVIO_SOURCE`) para que un `SOURCE` cualquiera no se cuele.
- Secretos (tokens, API keys) siempre en el `.env`, nunca en el toml.

## Privacidad y datos

- **Caras difuminadas apenas se lee el frame** (`servio_vision/privacy.py`, enganchado en `capture.py`): nada de lo que viene después (pipeline, vista de demo, grabaciones, `out/`) ve una cara. Si agregás una forma nueva de leer frames, sumala como lector de `capture.open_source`, que es el único que envuelve los frames con `privacy.default_anonymizer()`; `CameraReader` solo entrega frames **crudos**, no lo uses directo para mostrar ni guardar. El modelo de YuNet se baja a `vision/models/` (ignorado); `FACE_MODEL` apunta a otra ruta.
- **No se guardan frames** salvo con `main.py --record DIR`, y esos también salen difuminados. Una herramienta que escribe imágenes en `out/` tiene que leerlas con `capture.open_source`.
- A Claude solo se mandan recortes de mesas y platos, no el salón entero.
- Datasets, pesos e imágenes nunca se suben al repo (`vision/data/`, `vision/models/` y `vision/out/` están en `.gitignore`):
  - Video de la demo grabado por el equipo: carpeta compartida del equipo.
  - Dataset *AI Smart Restaurant Surveillance* de Roboflow (CC BY 4.0): se baja en local para entrenar y evaluar; se cita en `vision/README.md`.
  - Videos de YouTube: solo para probar, no se muestran en la demo ni se redistribuyen.
