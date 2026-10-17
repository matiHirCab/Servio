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
  "camera_id": "cam-1",
  "ts": "2026-10-17T20:15:03.120Z",
  "tables": [
    {
      "table_id": "T1",
      "items": [
        { "track_id": "T1-p-3", "kind": "plate", "state": "comiendo",     "confidence": 0.82, "fill_ratio": 0.55, "bbox": [210,140,330,250] },
        { "track_id": "T1-d-1", "kind": "drink", "state": "vacia",        "confidence": 0.71, "bbox": [400,120,440,170] }
      ]
    },
    { "table_id": "T2", "items": [] }
  ]
}
```

- `track_id`: estable mientras el objeto siga en la mesa (lo da el tracker). Si desaparece >30 s se considera retirado.
- `fill_ratio`: 0–1, opcional, estimación de cuánta comida queda. Útil para métricas y para ajustar umbrales.
- `state` ya viene **suavizado** (ver [04-vision.md](04-vision.md)).
- Respuesta: `202 { "ok": true }`.

## Backend → Dashboard

`GET /api/tables`

```json
[
  {
    "table_id": "T1", "name": "Mesa 1", "status": "por_terminar",
    "items": [ { "track_id": "T1-p-3", "kind": "plate", "state": "por_terminar", "state_since": "…", "first_seen": "…" } ],
    "open_alerts": 1, "last_update": "…"
  }
]
```

`status` de mesa: `libre` (sin ítems), `servida` (mayoría lleno), `comiendo`, `por_terminar`, `terminada` (todo vacío).

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
tables        (table_id PK, name, waiter_chat_id)
items         (track_id PK, table_id, kind, state, fill_ratio, first_seen, state_since, last_seen, removed_at)
observations  (id, ts, table_id, payload jsonb)          -- crudo, para métricas y debug
alerts        (id PK, table_id, type, message, status, created_at, acked_by, acked_at, resolved_at)
waiters       (id, name, telegram_chat_id, whatsapp_number)
```
