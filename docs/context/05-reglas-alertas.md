# 05 — Reglas de avisos

Viven en `vision/servio_vision/alerts/` (Matías). Se evalúan cada vez que llega una observación de mesa. Umbrales en `alerts/config.py`, con valores de producción y de demo (`DEMO_MODE` / `--demo`). Todos son configurables (ver «Umbrales configurables»).

## Avisos

| Aviso | Se abre cuando | Mensaje | Producción | Demo |
|---|---|---|---|---|
| `mesa_terminada` | La mesa queda **terminada**: todos sus platos en `vacio` (ver «Estado de mesa»). Se cierra **solo** si retiran los platos (mesa `libre`) o sirven algo nuevo | "🍽️ Mesa 3: terminó." | — | — |
| `plato_vacio` | Un plato está en `vacio` hace ≥ T1 **mientras el resto de la mesa sigue comiendo** (la mesa no está `terminada`). Ya abierto, se cierra cuando retiran ese plato | "🧹 Mesa 3: hay platos vacíos para retirar." | T1 = 60 s | 2 × `interval` |
| `plato_lleno_mucho` | Un plato está en `lleno` hace ≥ T2 desde que apareció **y** `people_present` no es `false` | "❓ Mesa 3: un plato sigue lleno hace 12 min. ¿Está todo bien?" | T2 = 10 min | 2 × `interval` |

## Plato y mozo en los avisos, y respuestas del mozo

Con los datos del restaurante (`fixtures/restaurante/`, simulados para la demo; en producción, la comanda), los avisos empiezan con el emoji, el mozo y la mesa ("🍽️ Juan · Mesa 3: terminó.") y el de `plato_lleno_mucho` nombra el plato: "❓ Juan · Mesa 3: Risotto de hongos sigue lleno hace 12 min. ¿Está todo bien? Pasá a preguntar y contanos qué pasó." Ese aviso trae botones con las respuestas típicas del plato y "✏️ Otra respuesta"; la respuesta se agrega al mensaje y queda en el log (`alert_feedback`, ver `03`). Un "plato" que no corresponde a nada del pedido de la mesa no cuenta para los avisos.

## Recordatorio y cierre

El mozo no responde, así que el antispam es por tiempo:

- **Un aviso abierto por `(mesa, tipo)`.** Mientras esté abierto no se manda otro igual.
- **Recordatorio:** si la condición sigue después de R minutos, se manda un recordatorio ("Mesa 3: los platos vacíos siguen sin retirar hace 4 min"). Producción R = 3 min, demo 2 × `interval`. Máximo 2 recordatorios.
- **Cierre:** cuando la condición deja de cumplirse (retiraron el plato, retiraron los platos de la mesa terminada, sirvieron algo nuevo), el aviso pasa a `resuelta` y se edita el mensaje original con "✅ ya resuelto". Que la mesa pase de `por_terminar` a `terminada` **no** cierra nada: no es que alguien hizo algo.
- **Mesa terminada y platos retirados:** cuando la mesa queda `libre`, el mensaje "terminó" se edita a "✅ Juan retiró los platos 1 min 20 s después de que terminó la mesa." (en vez de "ya resuelto"). En la base queda `avisos.motivo_cierre` = `retirados` (o `servido` si llegó comida nueva), y `/metricas` promedia por mozo solo los retiros.
- **`por_terminar` no avisa:** es solo el color de la mesa en la vista. El mozo no va mientras todavía comen; va cuando la mesa terminó (`mesa_terminada`), y en un solo viaje retira y ofrece postre, café o la cuenta.
- **Agrupar:** si en la misma evaluación se abren varios avisos de la misma mesa, se manda un solo mensaje con todo.

## Umbrales configurables

Los umbrales cuentan **tiempo de video** (el `ts` de las observaciones), así que `--speed` acelera la demo en el reloj pero no la acorta: con un video corto, lo que hace falta son umbrales cortos.

- **Producción:** T1 = 60 s, T2 = 10 min, R = 3 min.
- **Demo (`--demo`):** cada umbral vale por defecto `2 × interval`, lo mínimo que el suavizado puede confirmar. Con `--interval 30` son 60 s; con `--interval 15`, 30 s (para videos cortos).
- **A mano:** `--t1`, `--t2` y `--reminder` (o `ALERT_T1`, `ALERT_T2`, `ALERT_REMINDER` en `.env`) fijan cualquier umbral; el flag le gana a la variable.
- `config.py` recibe el `interval` del loop y no arranca si algún umbral queda por debajo de `2 × interval`.

## Umbrales y cadencia

Las observaciones llegan cada 30 s (en un video, 30 s del video; ver [04-vision.md](04-vision.md)) y el suavizado exige 2 lecturas para aceptar un cambio, así que el tiempo mínimo observable es ≈ 2 × cadencia. Todo umbral de demo debe ser ≥ 2 × la cadencia; `config.py` lo valida al arrancar.

## `people_present`

- Si es `false`, no se abre `plato_lleno_mucho`.
- Un `plato_lleno_mucho` abierto se cierra por falta de gente recién con **2 lecturas seguidas** con `people_present: false`. Una sola lectura sin gente (el detector no vio a alguien sentado) no lo cierra, para no cerrar y reabrir el aviso por un parpadeo.
- Si es `false` y hay platos vacíos, `plato_vacio` sigue (hay que retirar).

## Estado de mesa

Función pura `table_status(items)`, evaluada en este orden:

1. sin platos → `libre`
2. todos `vacio` → `terminada`
3. ≥ 50 % de los platos en `por_terminar` o `vacio` → `por_terminar`
4. ≥ 50 % de los platos en `lleno` → `servida`
5. resto → `comiendo`

## Estado por plato

`alerts/state.py` (funciones puras `update_table` y `table_status`) guarda cada plato por su `track_id` actual, con `first_seen`, `state`, `state_since` y `last_seen`.

Cuando el clasificador cambia el id de un plato (`p1 → p4`), el suavizado manda los dos juntos una lectura y después solo el nuevo. Para no reiniciar el tiempo del plato y no mezclar platos distintos:

- **Provisional:** un id nuevo en la `position` de un plato que sigue en la mesa no cuenta para las reglas en esa lectura (`active_plates()` lo deja afuera), así no cambia el estado de la mesa ni dispara avisos.
- **Herencia:** cuando un plato deja de venir y hay un **único** plato reciente (apareció en esa lectura o en la anterior) en su misma `position`, ese plato hereda `first_seen`, y también `state_since` si tiene el mismo estado.
- **Solo si puede ser el mismo plato:** el candidato tiene que haber aparecido en esa `position` (no vale uno que se corrió ahí) y tener el mismo estado o un paso hacia adelante. Un retroceso o un salto (`smoothing.is_hard_change`, por ejemplo `vacio → lleno` cuando cambian el principal por el postre) es otro plato y cuenta con sus propios tiempos.
- **Dos platos:** si el viejo y el nuevo siguen viniendo, eran dos platos (por ejemplo, sirvieron otro en el mismo casillero): el nuevo cuenta desde la lectura siguiente, con sus propios tiempos, y nunca hereda.
- **Ambigüedad:** con dos candidatos, o dos platos retirados en la misma `position`, nadie hereda.
- **Retirado:** un plato que no viene en la observación ya está retirado; el suavizado lo saca después de 2 lecturas ausente ([03-contratos.md](03-contratos.md)).
- **Riesgo conocido:** si cambian un plato por otro en el mismo casillero y el estado del nuevo es igual o un paso adelante del viejo (por ejemplo, `lleno` por `lleno`), el nuevo hereda el tiempo del viejo. Los ids estables de la visión (emparejados por `bbox`) hacen que esto pase solo cuando falla ese emparejamiento.

## Tests

El motor es una función pura en `alerts/engine.py`: `evaluate(table, now, config, open_alerts) → EngineResult(opened, reminded, resolved)`, por mesa.

- Usa `table.active_plates()` (sin los provisionales) y los umbrales de `config.py`.
- Recordatorios a `created_at + R × n`, hasta `max_reminders`.
- Las lecturas seguidas sin gente las cuenta `TableState.people_absent_readings` (`alerts/state.py`).
- `alerts.process(observation)` (`alerts/processor.py`) une todo: actualiza el estado, corre el motor, manda, recuerda o edita, y escribe el log. Los avisos de una mesa que se abren en la misma evaluación van en un solo mensaje, y lo mismo sus recordatorios (en un mensaje nuevo). Al cerrar se edita el mensaje en el que se abrió: si ya están cerrados todos sus avisos, con "✅ ya resuelto" al final; si no, solo la línea cerrada lleva "✅".
- Si Telegram falla al abrir, el aviso queda abierto sin `telegram_message_id` (no se reintenta) y al cerrarse no se edita nada.

Los tests (`tests/test_alert_engine.py`) reproducen `fixtures/observations/*.jsonl` lectura por lectura con los umbrales de producción y comparan exactamente contra `fixtures/expected/*.alerts.json` (pytest).
