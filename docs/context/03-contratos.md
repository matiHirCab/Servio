# 03 — Contratos (fuente de verdad)

Todo lo que cruza entre módulos está definido acá. Si lo cambiás, cambialo acá primero.

## Enums

```ts
type ItemKind   = "plate" | "drink";
type PlateState = "lleno" | "comiendo" | "por_terminar" | "vacio";
type DrinkState = "llena" | "media" | "vacia";
type AlertType  = "mesa_por_terminar" | "plato_vacio" | "plato_lleno_mucho" | "bebida_vacia";
type AlertStatus = "abierta" | "en_camino" | "resuelta" | "expirada";
```

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
        { "track_id": "T1-p3", "kind": "plate", "state": "comiendo",     "confidence": 0.82, "fill_ratio": 0.55, "position": "arriba-izquierda" },
        { "track_id": "T1-d1", "kind": "drink", "state": "vacia",        "confidence": 0.71, "position": "centro" }
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

1. Un ítem que **desaparece** y, en la misma lectura, **aparece** otro con el mismo `kind` y la misma `position` en esa mesa, se trata como el **mismo objeto**: se conserva el `track_id` viejo, `first_seen` y `state_since` no se reinician.
2. Si no hay coincidencia, el ítem nuevo es un objeto nuevo (`first_seen = ts`).
3. Un ítem ausente en 2 lecturas seguidas se marca `removed_at`.

Se registra cuántas veces se aplicó la regla 1 (métrica de calidad de la visión).

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

`GET /api/metrics?from=…&to=…` → `{ avg_meal_minutes, avg_response_seconds, alerts_by_type, plates_returned_full }`.

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

## Telegram → Backend: `POST /api/telegram/webhook`

Callback data de los botones: `ack:<alert_id>` ("Voy") y `done:<alert_id>` ("Listo"). Se valida con el header `X-Telegram-Bot-Api-Secret-Token`.

## Tablas de base de datos (mínimo)

```
tables        (table_id PK, name, people_present)
table_waiters (table_id, waiter_id)                      -- una mesa puede tener varios mozos y viceversa
items         (track_id PK, table_id, kind, state, fill_ratio, first_seen, state_since, last_seen, removed_at)
observations  (id, ts, table_id, payload jsonb)          -- crudo, para métricas y debug
alerts        (id PK, table_id, type, message, status, created_at, acked_by, acked_at, resolved_at)
waiters       (id, name, telegram_chat_id, whatsapp_number)
```
