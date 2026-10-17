# 03 — Contratos (fuente de verdad)

Todo lo que cruza entre las partes de cada persona está definido acá. Si lo cambiás, cambialo acá primero, junto con los fixtures, y que revise el dueño de la otra parte.

## Enums

```python
ItemKind   = Literal["plate"]                                        # solo platos de comida
PlateState = Literal["lleno", "comiendo", "por_terminar", "vacio"]
AlertType  = Literal["mesa_por_terminar", "plato_vacio", "plato_lleno_mucho"]
AlertStatus = Literal["abierta", "resuelta"]
```

Estado según la comida que queda (`fill_ratio`, de 0 a 1), en `servio_vision/schemas.py`:
`lleno` > 0,80 · `comiendo` 0,30–0,80 · `por_terminar` 0,05–0,30 · `vacio` < 0,05.

## Zonas de mesas — `vision/config/zones.json`

Coordenadas en píxeles del frame original. Se dibujan una vez con `vision/tools/draw_zones.py`.

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
| `position` | str | Texto libre ("arriba-izquierda"), para mensajes y debug. |
| `bbox` | [x, y, w, h] \| None | **Nuevo.** Caja del plato, normalizada 0–1 dentro del recorte de la mesa. La da YOLO; sirve para emparejar ids y para la vista de demo. |
| `confidence` | float \| None | |
| `visible` | bool | `false` si algo lo tapa; no cuenta como lectura en el suavizado. |

`TableReport`: `items: list[Item]`, `people_present: bool` (si falta se asume `true`), `notes: str | None`.

Implementaciones:
- `fake` (Jessica): sin modelo, para armar el pipeline.
- `yolo` (Tomás): solo YOLO; estado `lleno` o `vacio` según la clase detectada. Plan B sin internet.
- `hybrid` (Tomás): YOLO encuentra los platos y Claude estima `fill_ratio` de cada uno. Si Claude falla, usa lo de YOLO. **Es el de la demo.**
- `claude` (Tomás): solo Claude con el recorte de la mesa entera. Respaldo si YOLO no está entrenado.

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

- `ts`: en un video, el tiempo del video (no el reloj), para que los umbrales tengan sentido.
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
    created_at: datetime
    status: AlertStatus     # "abierta" | "resuelta"
    reminders_sent: int
    telegram_message_id: int | None
```

Telegram: `sendMessage` al grupo `TELEGRAM_GROUP_CHAT_ID` al abrir o recordar; `editMessageText` con "✅ ya resuelto" al cerrar. Ver [06-notificaciones.md](06-notificaciones.md).

## Fixtures — `fixtures/`

Ejemplos concretos de este contrato para probar sin video ni modelo:

- `fixtures/observations/*.jsonl`: una observación de mesa por línea (formato de arriba). Escenarios mínimos: mesa que pasa de lleno a vacío y se retira, plato lleno 12 min con gente y después sin gente, id que cambia en la misma posición.
- `fixtures/expected/*.alerts.json`: avisos que tienen que salir para cada escenario, con el segundo en que salen.

## Log — `vision/out/eventos.jsonl`

Una línea por observación (`{"event": "observation", ...}`) y por aviso (`{"event": "alert_opened" | "alert_reminder" | "alert_resolved", ...}`). Sirve para revisar una corrida y para reproducirla con `vision/tools/replay.py`.
