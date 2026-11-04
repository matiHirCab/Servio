# 03 — Contratos (fuente de verdad)

Todo lo que cruza entre las partes de cada persona está definido acá. Si lo cambiás, cambialo acá primero, junto con los fixtures, y que revise el dueño de la otra parte.

## Enums

```python
ItemKind   = Literal["plate"]                                        # solo platos de comida
PlateState = Literal["lleno", "comiendo", "por_terminar", "vacio"]
AlertType  = Literal["mesa_terminada", "plato_vacio", "plato_lleno_mucho"]
AlertStatus = Literal["abierta", "resuelta"]
```

Estado según la comida que queda (`fill_ratio`, de 0 a 1), en `servio_vision/schemas.py`:
`lleno` ≥ 0,75 · `comiendo` 0,30–0,75 · `por_terminar` 0,05–0,30 · `vacio` < 0,05.

## Zonas de mesas — `vision/config/zones.json`

Coordenadas en píxeles del frame original. Opcional: sin `--zones`, las mesas se detectan solas en cada lectura y se arman zonas con este mismo formato (`servio_vision/tables.py`). Con `--zones`, se dibujan una vez con `vision/tools/draw_zones.py`.

```json
{
  "camera_id": "cam-1",
  "frame_size": [1920, 1080],
  "tables": [
    { "table_id": "T1", "name": "Mesa 1", "polygon": [[100,80],[520,80],[520,400],[100,400]] },
    { "table_id": "T2", "name": "Mesa 2", "polygon": [[640,80],[1180,80],[1180,400],[640,400]] }
  ]
}
```

## Interfaz `Classifier` (Tomás ↔ Jessica)

`vision/servio_vision/classifier.py` y `schemas.py`:

```python
class Classifier(Protocol):
    def classify(self, image: bytes, table_id: str, previous_items: list[Item]) -> TableReport | None: ...

def get_classifier(name: str, **kwargs) -> Classifier: ...   # "fake" | "yolo" | "hybrid" | "claude"
```

- `image`: recorte JPEG de la mesa, sacado del frame original (sin achicar antes).
- `previous_items`: los platos de la lectura anterior de esa mesa, para mantener ids.
- `None` = lectura fallida: se descarta y no cuenta para el suavizado.

`Item` (un plato):

| Campo | Tipo | Nota |
|---|---|---|
| `id` | str | Estable entre lecturas (`p1`, `p2`…). Lo asigna el clasificador emparejando por posición con `previous_items`. |
| `kind` | `"plate"` | |
| `state` | PlateState \| None | Si viene `fill_ratio`, se calcula de ahí. |
| `fill_ratio` | float \| None | Comida que queda, 0–1. |
| `position` | str | Etiqueta fija de una grilla 3×3 ("arriba-izquierda", "centro"…, `tracking.position_label`). Se usa en mensajes y debug, y `alerts/state.py` la compara por igualdad para reconocer un id cambiado (05, «Estado por plato»): tiene que salir siempre de la misma grilla, no texto libre. |
| `bbox` | [x, y, w, h] \| None | **Nuevo.** Caja del plato, normalizada 0–1 dentro del recorte de la mesa. La da YOLO; sirve para emparejar ids y para la vista de demo. |
| `confidence` | float \| None | |
| `visible` | bool | `false` si algo lo tapa; no cuenta como lectura en el suavizado. |

`TableReport`: `items: list[Item]`, `people_present: bool` (si falta se asume `true`), `notes: str | None`.

Implementaciones:
- `fake` (Jessica): sin modelo, para armar el pipeline.
- `yolo` (Tomás): solo YOLO; estado `lleno` o `vacio` según la clase detectada. Plan B sin internet.
- `hybrid` (Tomás): YOLO encuentra los platos y Claude estima `fill_ratio` de cada uno. Si Claude falla, usa lo de YOLO. **Es el de la demo.**
- `claude` (Tomás): solo Claude con el recorte de la mesa entera, sin detector de platos: el modelo ubica cada plato en la grilla 3×3 (`position`) y los ids se siguen por esa posición; `bbox` va vacío. Respaldo si no hay YOLO. **Manda la imagen de la mesa**, lo que C-3 sacó del camino normal, así que es **opt-in**: solo con `CLAUDE_ALLOW_TABLE_IMAGES=true` y con el detector local de personas (lo que de ellas entre en la mesa sale pixelado; las caras ya vienen difuminadas de la captura). Sin eso no arranca (`ClassifierUnavailable`). Anda con `cli` y `anthropic`, no con `openai`.

## Observación de mesa (Jessica → Matías)

Lo que el pipeline le pasa a `alerts` después de suavizar, una por mesa y por lectura. Es una llamada de función en el mismo proceso (`alerts.process(observation)`), y la misma forma es la que se escribe en el log y en los fixtures.

```json
{
  "schema_version": 1,
  "camera_id": "cam-1",
  "ts": "2026-10-17T20:15:03.120Z",
  "table_id": "T1",
  "people_present": true,
  "items": [
    { "track_id": "T1-p3", "state": "comiendo", "fill_ratio": 0.55, "confidence": 0.82, "position": "arriba-izquierda" }
  ]
}
```

- `ts`: en un video, el tiempo del video (no el reloj), para que los umbrales tengan sentido. Siempre **con zona horaria** (UTC): un `datetime` sin zona se rechaza. En JSON va como `2026-10-17T20:15:03.120Z` (con `T`, segundos y `Z` o `±HH:MM`).
- `track_id`: `{table_id}-{id del Item}`.
- Solo platos visibles y ya suavizados. Un plato ausente en 2 lecturas seguidas deja de venir.
- Cadencia: una observación por mesa cada **30 s** (`--interval`), haya cambiado o no la mesa. En un video, son 30 s del video: con `--speed` se ven más rápido.

## Aviso (Matías → Telegram)

```python
class Alert(BaseModel):
    id: str
    table_id: str
    type: AlertType
    message: str            # texto que se manda
    created_at: datetime   # con zona horaria (UTC), igual que ts
    status: AlertStatus     # "abierta" | "resuelta"
    reminders_sent: int
    telegram_message_id: int | None
```

Telegram: `sendMessage` al grupo `TELEGRAM_GROUP_CHAT_ID` al abrir o recordar; `editMessageText` con "✅ ya resuelto" al cerrar. Ver [06-notificaciones.md](06-notificaciones.md).

## Fixtures — `fixtures/`

Ejemplos concretos de este contrato para probar sin video ni modelo:

- `fixtures/observations/*.jsonl`: una observación de mesa por línea (formato de arriba). Escenarios mínimos: mesa que pasa de lleno a vacío y se retira, plato lleno 12 min con gente y después sin gente, id que cambia en la misma posición.
- `fixtures/expected/*.alerts.json`: avisos que tienen que salir para cada escenario, con el segundo en que salen. Una lista de eventos en orden, con los nombres del log: `{ "at_seconds": 130, "event": "alert_opened" | "alert_reminder" | "alert_resolved", "type": "mesa_terminada" | "plato_vacio" | "plato_lleno_mucho", "table_id": "T1" }`, con `at_seconds` contado desde la primera línea del `.jsonl` (umbrales de producción).

Los modelos de la observación de mesa (`TableObservation`, `ObservedPlate`) y del aviso (`Alert`) están en `vision/servio_vision/alerts/models.py`. Rechazan campos desconocidos y un `state` que no coincide con su `fill_ratio`.

## Log — `vision/out/eventos.jsonl`

Con datos del restaurante, lo mismo queda también en una base relacional (`vision/out/servio.db`, esquema en `vision/servio_vision/db.py`). Además de observaciones y avisos, las respuestas de los mozos al aviso de plato lleno: `{"event": "alert_feedback", "ts", "alert_id", "table_id", "type", "dish", "waiter", "answer", "free_text", "answered_by", "seconds_to_answer"}`.


Una línea JSON por evento, en el orden en que pasan. Lo escribe `alerts.process()` (`EventLogOutput` en `alerts/outputs.py`). El archivo se **agrega** (no se pisa): varias corridas quedan una detrás de otra; borralo antes de la corrida de la demo si querés una sola.

- Inicio de corrida: `{"event": "run_started", "started_at": ..., "config": {"interval": 30, "t1": 60, "t2": 600, "reminder": 180, "max_reminders": 2}}`, la primera línea que escribe cada corrida (al hacer `alerts.configure()`), con la hora real y los umbrales resueltos (`AlertConfig`). `replay.py` corta el archivo acá y reproduce con esos umbrales.
- Observación: `{"event": "observation", ...}` con los campos de la observación de mesa. Sin `event`, la línea valida como `TableObservation`. Se escribe antes de aplicarla, así queda aunque `alerts` la rechace (por ejemplo, un `ts` que va hacia atrás).
- Aviso: `{"event": "alert_opened" | "alert_reminder" | "alert_resolved", "ts": ..., ...}`: `ts` de la observación que lo produjo, los campos del `Alert` (ya con `status`, `reminders_sent` y `telegram_message_id` de ese momento) y `text`, lo que se mandó (abrir, recordar) o cómo quedó el mensaje editado (cerrar); `null` al cerrar un aviso cuyo mensaje no se pudo mandar (no hubo nada que editar). Cada aviso se escribe apenas se manda o edita.

```json
{"event": "alert_opened", "ts": "2026-10-17T20:07:00Z", "id": "T1-plato_vacio-1792267620", "table_id": "T1", "type": "plato_vacio", "message": "🧹 Mesa 1: hay platos vacíos para retirar.", "created_at": "2026-10-17T20:07:00Z", "status": "abierta", "reminders_sent": 0, "telegram_message_id": 2, "text": "🧹 Mesa 1: hay platos vacíos para retirar."}
```

Sirve para revisar una corrida, para reproducirla con `vision/tools/replay.py` (toma las líneas `run_started` y `observation`, corta el archivo en corridas y por defecto reproduce la última con sus umbrales; su propio log va a `vision/out/replay.jsonl`) y para el resumen de la demo.
