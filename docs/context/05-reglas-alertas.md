# 05 — Reglas de avisos

Viven en `vision/servio_vision/alerts/` (Matías). Se evalúan cada vez que llega una observación de mesa. Umbrales en `alerts/config.py`, con valores de producción y de demo (`DEMO_MODE` / `--demo`). Todos son configurables (ver «Umbrales configurables»).

## Avisos

| Aviso | Se abre cuando | Mensaje | Producción | Demo |
|---|---|---|---|---|
| `mesa_terminada` | La mesa queda **terminada**: todos sus platos en `vacio` (ver «Estado de mesa»). Se cierra **solo** si retiran los platos (mesa `libre`) o sirven algo nuevo | "Mesa 3 🍽️ terminó" | — | — |
| `plato_vacio` | Un plato está en `vacio` hace ≥ T1 **mientras el resto de la mesa sigue comiendo** (la mesa no está `terminada`). Ya abierto, se cierra cuando retiran ese plato | "Mesa 3 🧹 hay platos vacíos para retirar" | T1 = 60 s | 2 × `interval` |
| `plato_lleno_mucho` | Un plato está en `lleno` hace ≥ T2 desde que apareció **y** `people_present` no es `false` | "Mesa 3 ❓ un plato sigue lleno hace 12 min. ¿Está todo bien?" | T2 = 10 min | 2 × `interval` |

Cada mensaje empieza con la mesa, que Telegram muestra en **negrita** (decisión 5): es lo primero que se lee de reojo en el reloj. El mozo va al final de la primera oración.

## Plato y mozo en los avisos, y respuestas del mozo

Con los datos del restaurante (`fixtures/restaurante/`, simulados para la demo; en producción, la comanda), los avisos nombran al mozo de la mesa ("Mesa 3 🍽️ terminó · Juan") y el de `plato_lleno_mucho` nombra el plato: "Mesa 3 ❓ Risotto de hongos sigue lleno hace 12 min · Juan. ¿Está todo bien? Pasá a preguntar y contanos qué pasó." Un "plato" que no corresponde a nada del pedido de la mesa no cuenta para los avisos.

Todos los avisos traen botones (C-5, decisiones 5 y 6): **Voy**, **Listo** y **No hacía falta**. El de `plato_lleno_mucho` suma las respuestas típicas del plato y "✏️ Otra respuesta" (el motivo, como en A-4). Contestar es opcional: la cámara cierra los avisos sola.

| Respuesta | Qué hace |
|---|---|
| **Voy** | Toma el aviso (`answered_by`, quién) y **frena los recordatorios y el escalado**. Un segundo «Voy» de otro contesta «Ya lo tomó …». |
| **Listo** | Anota `done_at` (el mozo dice que ya está). Si la cámara lo ve resuelto, cierra como siempre; si a los **10 min** (`done_wait`) sigue igual, **insiste una vez** ("Mesa 1 🍽️ dijiste «Listo» hace 10 min y los platos siguen en la mesa") y, R después, **escala**. |
| Un motivo (plato lleno) | Queda en `alert_feedback` como en A-4 y también toma el aviso (frena los recordatorios). |
| **No hacía falta** | El **único cierre a mano**: el aviso pasa a `resuelta` con `motivo_cierre` / `close_reason` = `no_hacia_falta`. Cuenta como falso aviso (`adopcion`) y no entra en los tiempos. |

Cada respuesta se agrega al mensaje ("👟 Voy · Ana", "✅ Listo · Ana", "📝 No le gustaron") y el teclado queda con lo que todavía se puede tocar; al cerrarse el aviso se le sacan los botones. Todo queda en el log (`alert_feedback`, con `action`, ver `03`) y en la base (`respuestas.accion`).

**Responder escribiendo.** Una respuesta que **cita** el aviso, o uno de sus recordatorios o escalados (la respuesta rápida del reloj y del celular llega así), va a ese aviso: «voy», «ya voy», «👍» → Voy; «listo», «hecho», «ya está», «✅» → Listo; «no hacía falta», «falso», «❌» → No hacía falta. Se comparan sin mayúsculas, tildes ni puntuación y **enteras**, nunca por prefijo. Otro texto citando un aviso de plato lleno es su motivo; citando otro aviso, se ignora. Una respuesta **suelta**, sin cita, se ignora en el grupo: no se sabe de quién ni para qué mesa es (decisión 5). En el chat privado de un mozo irá a su aviso abierto más reciente, cuando exista ese chat (C-7).

## Recordatorio, escalado y cierre

- **Un aviso abierto por `(mesa, tipo)`.** Mientras esté abierto no se manda otro igual. Cada aviso va en su propio mensaje, con sus botones.
- **Recordatorio:** si nadie respondió y la condición sigue, a `created_at + R × n` se manda un recordatorio ("Mesa 3 🧹 los platos vacíos siguen sin retirar hace 4 min"), hasta `max_reminders` (por defecto 2). Producción R = 3 min, demo 2 × `interval`. Va en un mensaje nuevo, sin botones; citarlo responde al aviso.
- **Escalado:** si nadie respondió, sale cuando habría salido el recordatorio siguiente al último: a `created_at + R × (max_reminders + 1)` (R = 3 min y 2 recordatorios → **9 min**). "Mesa 2 ⚠️ sin atender hace 9 min · Sofía no respondió a 3 mensajes". Sale una sola vez (`escalated_at`). Va al chat del encargado (`encargado_telegram`, C-7); hasta que exista, y si no está configurado, al **grupo**, como un recordatorio más. Nunca a `TELEGRAM_ADMIN_CHAT_ID` (es el chat de operaciones). Tras «Listo», el escalado sale R después de la insistencia.
- **Cierre por la cámara:** cuando la condición deja de cumplirse (retiraron el plato, retiraron los platos de la mesa terminada, sirvieron algo nuevo), el aviso pasa a `resuelta` y se edita el mensaje original con "✅ ya resuelto". Gana siempre: si en la misma lectura tocaba un recordatorio o el escalado, solo se cierra. Que la mesa pase de `por_terminar` a `terminada` **no** cierra nada: no es que alguien hizo algo.
- **Mesa terminada y platos retirados:** cuando la mesa queda `libre`, el mensaje "terminó" se edita con "✅ Juan retiró los platos 1 min 20 s después de que terminó la mesa." (en vez de "ya resuelto"). En la base queda `avisos.motivo_cierre` = `retirados` (o `servido` si llegó comida nueva), y `/metricas` promedia por mozo solo los retiros.
- **Cierre por «No hacía falta»:** `motivo_cierre` = `no_hacia_falta`. Lo que la cámara ve mal lo va a seguir viendo, así que se **silencia**: en `plato_vacio` y `plato_lleno_mucho`, los platos que cumplían la condición en ese momento (por `track_id`, siguiendo el cambio de id), hasta que ese plato se va o cambia de estado; en `mesa_terminada`, la mesa, hasta que deja de estar terminada (la retiran o sirven algo). Otro plato de la misma mesa sí avisa. Los silencios se guardan con el estado (A-5).
- **`por_terminar` no avisa:** es solo el color de la mesa en la vista. El mozo no va mientras todavía comen; va cuando la mesa terminó (`mesa_terminada`), y en un solo viaje retira y ofrece postre, café o la cuenta.

## Umbrales configurables

Los umbrales cuentan **tiempo de video** (el `ts` de las observaciones), así que `--speed` acelera la demo en el reloj pero no la acorta: con un video corto, lo que hace falta son umbrales cortos.

- **Producción:** T1 = 60 s, T2 = 10 min, R = 3 min, 2 recordatorios antes de escalar (escala a los 9 min), 10 min de «Listo».
- **Demo (`--demo`):** cada umbral de tiempo vale por defecto `2 × interval`, lo mínimo que el suavizado puede confirmar. Con `--interval 30` son 60 s; con `--interval 15`, 30 s (para videos cortos). La cantidad de recordatorios sigue siendo 2.
- **Por local:** en el `servio.toml` de la casa, sección `[avisos]` (`alert_t1`, `alert_t2`, `alert_reminder`, `alert_max_reminders`, `alert_done_wait`), o en el `.env` (`ALERT_T1`, `ALERT_T2`, `ALERT_REMINDER`, `ALERT_MAX_REMINDERS`, `ALERT_DONE_WAIT`); el toml le gana al `.env`. Los tiempos van en segundos. No hay flags de CLI para estos. El escalado no es un valor aparte: quien quiera escalar a los 5 min pone `alert_reminder = 150` y `alert_max_reminders = 1`.
- `config.py` recibe el `interval` del loop y no arranca si algún umbral de tiempo queda por debajo de `2 × interval`.

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

El motor es una función pura en `alerts/engine.py`: `evaluate(table, now, config, open_alerts, muted) → EngineResult(opened, reminded, resolved, escalated)`, por mesa.

- Usa `table.active_plates()` (sin los provisionales) y los umbrales de `config.py`.
- Recordatorios a `created_at + R × n`, hasta `max_reminders`; escalado a `created_at + R × (max_reminders + 1)`. Con `answered_by`, nada; con `done_at`, la insistencia a `done_at + done_wait` y el escalado R después. `muted` son los silencios de «No hacía falta».
- Las respuestas las aplica `AlertCore.respond(alert_id, action, who)` (`voy`, `listo`, `motivo`, `no_hacia_falta`), que solo con «No hacía falta» devuelve un evento (`AlertsResolved`). Llega desde el hilo del bot por `AlertProcessor.respond`, con el mismo lock que `process`.
- Las lecturas seguidas sin gente las cuenta `TableState.people_absent_readings` (`alerts/state.py`).
- `alerts.process(observation)` (`alerts/processor.py`) une todo. El núcleo (`alerts/core.py`, `AlertCore`) actualiza el estado, corre el motor y arma los avisos con su texto (`alerts/messages.py`), sin E/S: devuelve eventos (`TableUpdated`, `AlertsOpened`, `AlertsReminded`, `AlertsEscalated`, `AlertsResolved`). Los suscriptores (`alerts/outputs.py`) los reciben en orden: el canal (`TelegramOutput`, que manda, recuerda o edita y le pone el `message_id` al aviso), el log (`EventLogOutput`) y la base (`DatabaseOutput`). Cada aviso va en su propio mensaje con sus botones (`FeedbackDesk`, `alerts/feedback.py`); los recordatorios de una mesa en la misma lectura van juntos en un mensaje nuevo, y lo mismo los escalados. Al cerrar se edita el mensaje en el que se abrió, con "✅ ya resuelto" al final y sin botones.
- Si Telegram falla al abrir, el aviso queda abierto sin `telegram_message_id` (no se reintenta) y al cerrarse no se edita nada. Lo mismo si el canal lanza una excepción: no se reintenta, así un error que llega después de mandar no repite el aviso en cada lectura.

- Con base, el estado (mesas, avisos abiertos con su `message_id` y sus respuestas, platos servidos, silencios de «No hacía falta» y los botones de cada aviso abierto) se guarda en `estado_servicio` (`alerts/persist.py`) al abrir o cerrar un aviso y al aparecer un plato; cuando cambia algo que usan las reglas, a lo sumo una vez por `SAVE_RULES_EVERY_S` (1 s), en la primera lectura que llega pasado ese tiempo; y si no, cada `SAVE_STATE_EVERY_S`. Un corte brusco pierde como mucho los cambios que esperaban esa lectura. Con una cámara en vivo, al arrancar se sigue desde ahí si el guardado es reciente (`RESUME_MAX_AGE_S`); si no, de cero.

Los tests (`tests/test_alert_engine.py` el motor, `tests/test_alert_core.py` el núcleo solo, `tests/test_alert_crash.py` un corte a mitad de cada fixture) reproducen `fixtures/observations/*.jsonl` lectura por lectura con los umbrales de producción, aplican las respuestas de `fixtures/replies/*.jsonl` (si el escenario las tiene; el motor solo no las sabe aplicar y reproduce los demás) y comparan exactamente contra `fixtures/expected/*.alerts.json` (pytest). `tests/test_feedback.py` prueba cada botón y las respuestas citando el aviso con el Telegram falso (`FakeTelegram`).
