# 02 — Arquitectura

## Vista general

Un solo programa en Python, corriendo en una laptop:

```
 [Video o cámara]
       │ frame cada 30 s
       ▼
 ┌─────────────────────────────── vision/ (Python) ───────────────────────────────┐
 │ captura → recorte por mesa → clasificador → suavizado → estado de mesa → reglas │
 │                               (YOLO + Claude)                                   │
 └───────────────────────────────────────────────────────────┬────────────────────┘
                                                             │ sendMessage / editMessageText
                                                             ▼
                                                   [Telegram: grupo "Mozos"]
```

No hay backend web ni dashboard. El estado vive en memoria; se guarda un log en `vision/out/eventos.jsonl` y, con los datos del restaurante (`fixtures/restaurante/`), una base relacional para las métricas (`servio_vision/db.py`, SQLAlchemy Core con migraciones de Alembic): SQLite en `vision/out/servio.db`, o el Postgres compartido de `DATABASE_URL`, donde cada casa escribe con su `local_id` (A-6).

## Componentes

| Parte | Carpeta | Qué hace | Dueño |
|---|---|---|---|
| Configuración y armado | `servio_vision/{settings,cli,app}.py`, `vision/main.py`, `vision/config/servio.example.toml` | Un `Settings` por casa (flags > toml > `.env`), y el armado del pipeline a partir de él | Jessica |
| Pipeline | `servio_vision/{pipeline,capture,privacy,zones,smoothing,recording}.py` | Lee la fuente, difumina caras, recorta cada mesa, clasifica, suaviza, graba si se pidió | Jessica |
| Clasificador | `servio_vision/classifiers/` | YOLO encuentra los platos; Claude estima cuánta comida queda | Tomás |
| Reglas y avisos | `servio_vision/alerts/` | Estado de cada mesa, reglas, envío a Telegram, log | Matías |

Dueños por carpeta en [09-convenciones.md](09-convenciones.md).

## Por qué así

- **Un solo proceso:** el mozo no responde, así que no hace falta servidor ni webhook. Menos piezas que se rompen en la demo.
- **YOLO + Claude juntos:** YOLO (entrenado con el dataset de Roboflow) encuentra cada plato rápido y en la laptop, y da su posición, lo que permite mantener el mismo id para el mismo plato. Claude mira el recorte de cada plato y estima cuánta comida queda, cosa que YOLO no sabe hacer bien (solo distingue lleno/vacío). Si Claude no responde (sin internet), el clasificador usa lo que dice YOLO.
- **Interfaz única `Classifier`:** el pipeline no sabe qué modelo usa. Hay tres implementaciones: `fake` (sin modelo, para desarrollar), `yolo` (solo YOLO, plan B sin internet) y `hybrid` (YOLO + Claude, la de la demo).
- **Las reglas viven aparte de la visión:** se ajustan umbrales sin tocar el modelo, y se prueban con fixtures sin video ni Claude.

## Flujo de datos

1. `main.py` arma un `Settings` (flags, `--config casa.toml`, `.env`) y `app.run` lee un frame cada `interval` segundos del video o la cámara, ya con las caras difuminadas.
2. Recorta cada mesa según `config/zones.json`, desde la resolución original.
3. Si el recorte cambió (o pasó el refresco forzado), llama a `classifier.classify(recorte, table_id, items_anteriores)`, que devuelve un `TableReport` con los platos, su estado y su posición.
4. El suavizado acepta un cambio de estado solo si se repite (ver [04-vision.md](04-vision.md)).
5. Con la lectura suavizada se arma una **observación** de la mesa (formato en [03-contratos.md](03-contratos.md)) y se pasa a `alerts`.
6. `alerts` actualiza el estado de la mesa (desde cuándo cada plato está en cada estado), corre las reglas y manda o edita mensajes de Telegram.
7. Cada observación y cada aviso se escriben en `vision/out/eventos.jsonl`.

## Recursos

| Recurso | Para qué |
|---|---|
| Claude Code (suscripciones Pro/Max) | Claude por `claude -p` (`CLAUDE_PROVIDER=cli`). No hay créditos de AWS. |
| API de Anthropic | Respaldo si se consigue una API key. |
| Colab con GPU | Entrenar YOLO (`yolov8n`). La inferencia corre en la laptop con CPU. |
| Roboflow | Dataset *AI Smart Restaurant Surveillance* (CC BY 4.0) para entrenar y evaluar. |
| Telegram | Bot que manda los avisos al grupo "Mozos". |
| GitHub | Repo, PRs y CI. |
