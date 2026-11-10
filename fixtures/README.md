# fixtures/

Observaciones de mesa de ejemplo y los avisos que tienen que salir, con el formato de [03-contratos.md](../docs/context/03-contratos.md). Sirven para probar las reglas y Telegram sin video ni modelo: tests del motor (MAT-3), `vision/tools/replay.py` (MAT-6), el test de punta a punta (`vision/tests/test_e2e_pipeline.py`, A-8) y plan B de la demo. Los valida `vision/tests/test_alert_models.py` contra `servio_vision/alerts/models.py`.

Dueño: Matías (ver [09-convenciones.md](../docs/context/09-convenciones.md)). Si cambiás el contrato, actualizá los fixtures en el mismo PR.

## `observations/*.jsonl`

Una observación de mesa por línea (`table_id` arriba), una lectura cada **30 s** desde `2026-10-17T20:00:00Z`, como el pipeline real (`--interval 30`, ver [04-vision.md](../docs/context/04-vision.md)). Como es lo que manda el pipeline después de suavizar, solo hay platos visibles, cada `fill_ratio` coincide con su estado, y un plato que deja de aparecer ya se considera retirado. En la tabla, los segundos se cuentan desde la primera línea, con los umbrales de **producción** de [05-reglas-alertas.md](../docs/context/05-reglas-alertas.md) (T1 = 60 s, T2 = 10 min, R = 3 min).

| Archivo | Qué pasa | Avisos esperados |
|---|---|---|
| `mesa_completa.jsonl` (T1, 27 líneas) | 0–30 s mesa vacía sin gente. A los 60 s llega gente, sin platos. 90–150 s `T1-p1` y `T1-p2` en `lleno`. 180–240 s `comiendo`. 270–330 s `por_terminar`. 360–480 s `vacio`. A los 510 s se retiran y la gente sigue (3 min). 720–780 s plato nuevo `T1-p3` en `lleno`. | `table_status`: `libre → servida → comiendo → por_terminar → terminada → libre → servida`. `por_terminar` no avisa. `mesa_terminada` se abre a los 360 s (los dos platos en `vacio`) y se resuelve a los 510 s (retiraron los platos). `plato_vacio` no se abre: los dos platos se vacían a la vez y avisa la mesa. |
| `plato_lleno_mucho.jsonl` (T2, 31 líneas) | `T2-p1` en `lleno` todo el tiempo. Hay gente de 0 a 780 s (13 min) y no hay desde los 810 s. | `plato_lleno_mucho` se abre a los 600 s, recordatorio a los 780 s (R = 3 min) y se resuelve a los 840 s (segunda lectura seguida con `people_present: false`; una sola no lo cierra). |
| `plato_vacio.jsonl` (T3, 21 líneas) | `T3-p1` está `comiendo` todo el tiempo. `T3-p2`: `lleno` 0–30 s, `comiendo` 60–90 s, `por_terminar` 120–150 s, `vacio` 180–420 s, y a los 450 s se lo llevan. La mesa nunca queda `terminada` (p1 sigue comiendo). | `plato_vacio` se abre a los 240 s (T1 = 60 s en `vacio`), recordatorio a los 420 s (R = 3 min) y se resuelve a los 450 s (retiraron ese plato). |
| `mesa_terminada_recordatorios.jsonl` (T4, 27 líneas) | `T4-p1` y `T4-p2` en `lleno` 0–60 s, `comiendo` 90–150 s y `vacio` de 180 a 720 s. Nadie los retira hasta los 750 s. | `mesa_terminada` se abre a los 180 s; recordatorios a los 360 s («terminó hace 3 min») y a los 540 s («hace 6 min»); a los 720 s, cuando habría salido el tercero, **escala** (nadie respondió a 3 mensajes, C-5); se resuelve a los 750 s con «Retiraron los platos 9 min 30 s después de que terminó la mesa». |
| `escalado.jsonl` (T4, 32 líneas) | Como el anterior, pero los platos vacíos siguen hasta los 870 s y se retiran a los 900 s. | `mesa_terminada` abre a 180 s, recordatorios a 360 y 540 s, escala a 720 s (R × 3) y una sola vez; se resuelve a los 900 s. |
| `boton_voy.jsonl` (T4, 27 líneas) + `replies/` | Las mismas lecturas que `mesa_terminada_recordatorios`. A los 240 s Lucía toca **Voy**. | `mesa_terminada` abre a 180 s y se resuelve a 750 s: sin recordatorios ni escalado («Voy» los frena). |
| `boton_listo.jsonl` (T4, 38 líneas) + `replies/` | Platos vacíos de 180 a 1050 s, retirados a los 1080 s. A los 240 s Lucía toca **Listo**. | Abre a 180 s; sin recordatorios hasta que pasan los 10 min de «Listo»: insiste una vez a los 840 s (`alert_reminder`), escala a los 1020 s (R después) y se resuelve a 1080 s. |
| `boton_no_hacia_falta.jsonl` (T4, 34 líneas) + `replies/` | Platos vacíos de 180 a 600 s, retirados a los 630 s. A los 240 s Lucía toca **No hacía falta**. A los 690 s llega `T4-p3` (`lleno`), `comiendo` desde 780 s y `vacio` a los 900 s; se lo llevan a los 960 s. | Abre a 180 s y se cierra a mano a 240 s (`no_hacia_falta`). La mesa sigue terminada hasta 600 s y **no** vuelve a avisar (silenciada); al retirarse deja de estarlo, así que cuando `T4-p3` se vacía abre otro `mesa_terminada` a los 900 s, que se resuelve a 960 s. |
| `id_cambiado.jsonl` (T1, 12 líneas) | `T1-p1` (`comiendo`, `arriba-izquierda`) hasta 150 s. A los 180 s llegan **juntos** `T1-p1` (el suavizado lo retiene una lectura) y `T1-p4` en la misma posición, como en una corrida real; desde 210 s solo `T1-p4`. `T1-p2` (`abajo-derecha`) deja de venir a los 330 s (como con el suavizado, que lo retiene una lectura). | Ninguno. En `alerts/state.py`, `T1-p4` es provisional a los 180 s (no cuenta para las reglas) y a los 210 s hereda los tiempos de `T1-p1` (`first_seen` y `state_since` = 0 s). `T1-p2` se retira a los 330 s. |

## `expected/<escenario>.alerts.json`

Uno por escenario: los eventos que tiene que producir `alerts` al reproducirlo, en orden, con los mismos nombres que el log `vision/out/eventos.jsonl`:

```json
{ "at_seconds": 360, "event": "alert_opened", "type": "mesa_terminada", "table_id": "T1" }
```

`event`: `alert_opened` | `alert_reminder` | `alert_escalated` | `alert_resolved`. `at_seconds` se cuenta desde la primera línea del `.jsonl`. Solo lo que hace el motor: las respuestas del mozo van aparte.

## `replies/<escenario>.jsonl`

Opcional (C-5): lo que contesta el mozo en ese escenario, una respuesta por línea:

```json
{"at_seconds": 240, "table_id": "T4", "type": "mesa_terminada", "action": "voy", "who": "Lucía"}
```

`action`: `voy` | `listo` | `no_hacia_falta` | `motivo`. Los tests la aplican **después** de procesar la lectura de ese segundo, al aviso abierto de esa mesa y ese tipo: `test_alert_core.py` con `AlertCore.respond`, `test_alert_process.py` y `test_alert_crash.py` como un toque del botón en el Telegram falso (`FeedbackDesk`). El motor solo (`test_alert_engine.py`) no sabe aplicarlas y reproduce los demás escenarios. Los valida `test_alert_models.py` (cada respuesta cae en una lectura y va a un aviso abierto).

## `pipeline/<escenario>.alerts.json`

Los avisos del mismo escenario cuando pasa por el **pipeline entero** (`main.py --classifier fake --fake-mode replay`): fuente, recorte, suavizado y alerts. Los compara `vision/tests/test_e2e_pipeline.py`, así que el CI falla si cambian. Mismo formato que `expected/`. El pipeline no aplica `replies/`: en los escenarios `boton_*` sale lo que pasaría si nadie contesta.

Difieren de `expected/` a propósito: las observaciones ya vienen suavizadas y el pipeline las vuelve a suavizar, así que un cambio de estado se confirma una lectura más tarde (casi todos los avisos se corren 30 s; `mesa_terminada` de T4 abre 60 s después). `plato_lleno_mucho` e `id_cambiado` salen iguales.

Si un cambio mueve los avisos a propósito, regenerarlos desde `vision/` y revisar el diff en el PR:

```bash
SERVIO_UPDATE_GOLDEN=1 pytest tests/test_e2e_pipeline.py
```
