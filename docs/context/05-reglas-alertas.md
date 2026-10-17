# 05 — Reglas de avisos

Viven en `vision/servio_vision/alerts/` (Matías). Se evalúan cada vez que llega una observación de mesa. Umbrales en `alerts/config.py`, con valores de producción y de demo (`DEMO_MODE` / `--demo`).

## Avisos

| Aviso | Se abre cuando | Mensaje | Producción | Demo |
|---|---|---|---|---|
| `mesa_por_terminar` | El estado de la mesa **pasa a** `por_terminar` (ver «Estado de mesa») | "🍽️ Mesa 3 está terminando." | — | — |
| `plato_vacio` | Un plato está en `vacio` hace ≥ T1 | "🧹 Mesa 3: hay platos vacíos para retirar." | T1 = 60 s | 10 s |
| `plato_lleno_mucho` | Un plato está en `lleno` hace ≥ T2 desde que apareció **y** `people_present` no es `false` | "❓ Mesa 3: un plato sigue lleno hace 12 min. ¿Está todo bien?" | T2 = 10 min | 45 s |

## Sin respuesta del mozo: recordatorio y cierre

El mozo no responde, así que el antispam es por tiempo:

- **Un aviso abierto por `(mesa, tipo)`.** Mientras esté abierto no se manda otro igual.
- **Recordatorio:** si la condición sigue después de R minutos, se manda un recordatorio ("Mesa 3: los platos vacíos siguen sin retirar hace 4 min"). Producción R = 3 min, demo 30 s. Máximo 2 recordatorios.
- **Cierre:** cuando la condición deja de cumplirse (retiraron el plato, la mesa dejó de estar por terminar), el aviso pasa a `resuelta` y se edita el mensaje original con "✅ ya resuelto".
- **Agrupar:** si en la misma evaluación se abren varios avisos de la misma mesa, se manda un solo mensaje con todo.

## Umbrales y cadencia

Las observaciones llegan cada 30 s (en un video, 30 s del video; ver [04-vision.md](04-vision.md)) y el suavizado exige 2 lecturas para aceptar un cambio, así que el tiempo mínimo observable es ≈ 2 × cadencia. Todo umbral de demo debe ser ≥ 2 × la cadencia; `config.py` lo valida al arrancar.

## `people_present`

- Si es `false`, no se abre `plato_lleno_mucho`.
- Si es `false` y hay platos vacíos, `plato_vacio` sigue (hay que retirar).

## Estado de mesa

Función pura `table_status(items)`, evaluada en este orden:

1. sin platos → `libre`
2. todos `vacio` → `terminada`
3. ≥ 50 % de los platos en `por_terminar` o `vacio` → `por_terminar`
4. ≥ 50 % de los platos en `lleno` → `servida`
5. resto → `comiendo`

## Estado por plato

`alerts/state.py` guarda por cada `track_id`: `first_seen`, `state`, `state_since` y `last_seen`. Si un `track_id` nuevo aparece en la misma mesa y `position` que uno que desapareció en esa misma lectura, se trata como el mismo plato (no reinicia `first_seen` ni `state_since`).

## Tests

El motor es una función pura `(estado_mesa, ahora, config, avisos_abiertos) → { abrir, recordar, cerrar }`. Los tests reproducen `fixtures/observations/*.jsonl` con tiempo simulado y comparan contra `fixtures/expected/*.alerts.json` (pytest).
