# 03 — Contratos (fuente de verdad)

Todo lo que cruza entre módulos está definido acá. Si lo cambiás, cambialo acá primero.

Los esquemas de `POST /api/observations` y de la alerta también viven en código: `web/lib/contracts.ts` (Zod) y `vision/servio_vision/contracts.py` (Pydantic). **Se cambian junto con este archivo y con `fixtures/`**; los tests de los dos módulos validan los fixtures contra esos esquemas.

## Enums

```ts
type ItemKind   = "plate";            // solo platos de comida: bebidas fuera de alcance
type PlateState = "lleno" | "comiendo" | "por_terminar" | "vacio";
// según fill_ratio (comida que queda): lleno > 0.80 · comiendo 0.30–0.80 · por_terminar 0.05–0.30 · vacio < 0.05
type AlertType  = "mesa_por_terminar" | "plato_vacio" | "plato_lleno_mucho" | "esperando_plato";
type AlertStatus = "abierta" | "en_camino" | "resuelta" | "expirada";
```

**Decidido en T0-1 ([11-tareas.md](11-tareas.md)):** entran las dos propuestas.
- `visible: boolean` por ítem, solo **dentro de la visión** (`report_table` / `TableReport`, ver [04-vision.md](04-vision.md)): la visión descarta los no visibles antes de suavizar, así que **no** viaja en `POST /api/observations`.
- `AlertType` `esperando_plato` (regla en [05-reglas-alertas.md](05-reglas-alertas.md)).

## Fixtures — `fixtures/`

Ejemplos concretos de este contrato, usados por los tres módulos para trabajar con mocks (lista de archivos en [11-tareas.md](11-tareas.md), T0-2). Si cambiás un contrato, actualizá los fixtures en el mismo PR.

- `fixtures/observations/*.jsonl`: una observación de `POST /api/observations` completa por línea. Es el formato que escribe la visión en `--dry-run` y el que reproduce `web/scripts/simulate.ts`.
- `fixtures/expected/*.alerts.json`: alertas que el motor tiene que producir para cada escenario: `{ at_seconds, type, table_id, expired_at_seconds? }`, con segundos contados desde la primera lectura (umbrales de producción).
- `fixtures/api/*.json`: respuestas de ejemplo de `GET /api/tables`, `/api/alerts` y `/api/metrics`.

## Zonas de mesas — `vision/config/zones.json`

Coordenadas en píxeles del frame de la cámara (se dibujan una vez con `vision/tools/draw_zones.py`).

```json
{
  "camera_id": "cam-1",
  "frame_size": [1280, 720],
  "tables": [
    { "table_id": "T1", "name": "Mesa 1", "polygon": [[100,80],[520,80],[520,400],[100,400]] },
    { "table_id": "T2", "name": "Mesa 2", "polygon": [[640,80],[1180,80],[1180,400],[640,400]] }
  ]
}
```

## Visión interna: interfaz `Classifier` (P1 ↔ P3)

Separa el modelo (P1) de la conexión de visión (P3). Vive en `vision/servio_vision/classifier.py`; los modelos Pydantic en `vision/servio_vision/schemas.py` siguen la herramienta `report_table` de [04-vision.md](04-vision.md).

```python
class Classifier(Protocol):
    def classify(self, image: bytes, table_id: str, previous_items: list[Item]) -> TableReport | None: ...

def get_classifier(name: str) -> Classifier: ...   # "fake" | "claude" | "yolo"
```

- `image`: recorte JPEG de la mesa (~512 px). `previous_items`: los ítems de la lectura anterior, para mantener ids.
- `None` = lectura fallida (se descarta, no cuenta para el suavizado).
- Implementaciones: `classifiers/fake.py` (P3, sin modelo), `classifiers/claude.py` (P1), `classifiers/yolo.py` (P1, solo si se decide en [04-vision.md](04-vision.md)). Se elige con `main.py --classifier`.

## Visión → Backend: `POST /api/observations`

Header: `Authorization: Bearer ${INGEST_TOKEN}`

```json
{
  "schema_version": 1,
  "camera_id": "cam-1",
  "ts": "2026-10-17T20:15:03.120Z",
  "tables": [
    {
      "table_id": "T1",
      "people_present": true,
      "items": [
        { "track_id": "T1-p3", "kind": "plate", "state": "comiendo",     "confidence": 0.82, "fill_ratio": 0.55, "position": "arriba-izquierda" }
      ]
    },
    { "table_id": "T2", "people_present": false, "items": [] }
  ]
}
```

- `schema_version`: entero. Hoy es `1`. El backend rechaza (`400`) versiones que no conoce. Se incrementa solo con cambios que rompen compatibilidad.
- Cadencia: una observación por mesa cada 15–30 s (modo demo: 5–10 s), no cada 2–5 s. Ver [04-vision.md](04-vision.md).

- `track_id`: `{table_id}-{id que devuelve Claude}` (ej. `T1-p3`). Claude mantiene el id entre lecturas porque le pasamos el estado anterior (ver [04-vision.md](04-vision.md)). Si falta en 2 lecturas seguidas se considera retirado.
- `position`: texto libre de Claude ("arriba-izquierda"), solo para debug y para que el dashboard dibuje aproximado.
- `people_present`: `true|false`, **campo de la mesa**. Si falta se asume `true` (no se suprimen alertas por falta de dato). Se guarda en `tables` y se expone en `GET /api/tables`.
- `fill_ratio`: 0–1, opcional, estimación de cuánta comida queda. Útil para métricas y para ajustar umbrales.
- `state` ya viene **suavizado** (ver [04-vision.md](04-vision.md)).
- Respuesta: `202 { "ok": true }`.

### Reconciliación de ids (backend)

Claude no garantiza ids estables. Antes de correr las reglas, el backend reconcilia los ítems de cada mesa:

1. Un ítem que **desaparece** y, en la misma lectura, **aparece** otro con el mismo `kind` y la misma `position` en esa mesa, se trata como el **mismo objeto**: se conserva el `track_id` viejo, `first_seen` y `state_since` no se reinician. El backend guarda el alias (id nuevo → `track_id` viejo, por ejemplo `T1-p4 → T1-p1`) y las lecturas siguientes con el id nuevo se mapean directo, sin volver a aplicar la regla.
2. Si no hay coincidencia, el ítem nuevo es un objeto nuevo (`first_seen = ts`).
3. Un ítem ausente en 2 lecturas seguidas se marca `removed_at`.

Se registra cuántas veces se aplicó la regla 1, es decir, cuántos alias nuevos se crearon (métrica de calidad de la visión). Un id que cambia una vez cuenta 1, aunque la visión lo siga mandando en las lecturas siguientes.

## Backend → Dashboard

`GET /api/tables`

```json
[
  {
    "table_id": "T1", "name": "Mesa 1", "status": "por_terminar",
    "items": [ { "track_id": "T1-p3", "kind": "plate", "state": "por_terminar", "state_since": "…", "first_seen": "…" } ],
    "people_present": true, "open_alerts": 1, "last_update": "…"
  }
]
```

`status` de mesa: `libre`, `servida`, `comiendo`, `por_terminar`, `terminada`. La definición exacta está en [05-reglas-alertas.md](05-reglas-alertas.md) («Estado de mesa») y es la **única** fuente: dashboard y alertas usan la misma función `tableStatus()`.

`GET /api/alerts?status=abierta` → lista de alertas.

`POST /api/alerts/:id/ack` → body `{ "waiter_id": "…" }` (opcional desde el dashboard). Pasa la alerta a `en_camino` y guarda `acked_by`, `acked_at`.

`POST /api/alerts/:id/resolve` → body `{ "waiter_id": "…" }` (opcional). Pasa la alerta a `resuelta` y guarda `resolved_at`.

Las dos responden `200` con la alerta actualizada y disparan `Notifier.update` para editar los mensajes ya enviados.

`GET /api/metrics?from=…&to=…` → `{ avg_meal_minutes, avg_response_seconds, alerts_by_type, plates_returned_full, id_reconciliations, avg_wait_between_courses }`.

- `id_reconciliations`: cuántas veces se aplicó la regla 1 de la reconciliación (calidad de la visión).
- `avg_wait_between_courses`: minutos promedio que una mesa con gente pasa sin platos entre que se retiran unos y llega el siguiente (diferencial "tiempo entre platos").

## Alerta

```json
{
  "id": "a_01H…",
  "table_id": "T1",
  "type": "mesa_por_terminar",
  "message": "Mesa 1 está terminando de comer. Ofrecé postre o café.",
  "created_at": "…",
  "status": "abierta",
  "acked_by": null, "acked_at": null, "resolved_at": null
}
```

## Backend → Notificaciones: interfaz `Notifier` (P2 ↔ P1)

Vive en `web/lib/notify/types.ts`. P2 la llama; P1 implementa `TelegramNotifier` en `web/lib/notify/telegram.ts`.

```ts
interface SentMessage { channel: "telegram"; chat_id: string; message_id: string }

interface Notifier {
  send(alert: Alert, waiter: Waiter): Promise<SentMessage>;
  update(alert: Alert, sent: SentMessage): Promise<void>;   // edita el mensaje según alert.status
}
```

- `send`: lo llama `POST /api/observations` por cada alerta **nueva** y cada mozo de la mesa (`table_waiters`). El resultado se guarda en `alert_messages`.
- `update`: lo llama el backend cuando la alerta cambia de estado: Voy/Listo (desde Telegram o el dashboard) o `expirada` (cuando el motor la devuelve en `expiradas`). Se llama una vez por fila de `alert_messages`.

## Telegram → Backend: `POST /api/telegram/webhook`

Callback data de los botones: `ack:<alert_id>` ("Voy") y `done:<alert_id>` ("Listo"). Se valida con el header `X-Telegram-Bot-Api-Secret-Token`.

## Tablas de base de datos (mínimo)

```
tables        (table_id PK, name, people_present)
table_waiters (table_id, waiter_id)                      -- una mesa puede tener varios mozos y viceversa
items         (track_id PK, table_id, kind, state, fill_ratio, first_seen, state_since, last_seen, removed_at)
observations  (id, ts, table_id, payload jsonb)          -- crudo, para métricas y debug
alerts        (id PK, table_id, type, message, status, created_at, acked_by, acked_at, resolved_at)
waiters       (id, name, telegram_chat_id)
alert_messages (alert_id, waiter_id, channel, chat_id, message_id, sent_at)   -- un mensaje por mozo; permite editarlo después
```
