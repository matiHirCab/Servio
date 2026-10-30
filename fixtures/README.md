# fixtures/

Observaciones de mesa de ejemplo y los avisos que tienen que salir, con el formato de [03-contratos.md](../docs/context/03-contratos.md). Sirven para probar las reglas y Telegram sin video ni modelo: tests del motor (MAT-3), `vision/tools/replay.py` (MAT-6) y plan B de la demo. Los valida `vision/tests/test_alert_models.py` contra `servio_vision/alerts/models.py`.

Dueño: Matías (ver [09-convenciones.md](../docs/context/09-convenciones.md)). Si cambiás el contrato, actualizá los fixtures en el mismo PR.

## `observations/*.jsonl`

Una observación de mesa por línea (`table_id` arriba), una lectura cada **30 s** desde `2026-10-17T20:00:00Z`, como el pipeline real (`--interval 30`, ver [04-vision.md](../docs/context/04-vision.md)). Como es lo que manda el pipeline después de suavizar, solo hay platos visibles, cada `fill_ratio` coincide con su estado, y un plato que deja de aparecer ya se considera retirado. En la tabla, los segundos se cuentan desde la primera línea, con los umbrales de **producción** de [05-reglas-alertas.md](../docs/context/05-reglas-alertas.md) (T1 = 60 s, T2 = 10 min, R = 3 min).

| Archivo | Qué pasa | Avisos esperados |
|---|---|---|
| `mesa_completa.jsonl` (T1, 27 líneas) | 0–30 s mesa vacía sin gente. A los 60 s llega gente, sin platos. 90–150 s `T1-p1` y `T1-p2` en `lleno`. 180–240 s `comiendo`. 270–330 s `por_terminar`. 360–480 s `vacio`. A los 510 s se retiran y la gente sigue (3 min). 720–780 s plato nuevo `T1-p3` en `lleno`. | `table_status`: `libre → servida → comiendo → por_terminar → terminada → libre → servida`. `por_terminar` no avisa. `mesa_terminada` se abre a los 360 s (los dos platos en `vacio`) y se resuelve a los 510 s (retiraron los platos). `plato_vacio` no se abre: los dos platos se vacían a la vez y avisa la mesa. |
| `plato_lleno_mucho.jsonl` (T2, 31 líneas) | `T2-p1` en `lleno` todo el tiempo. Hay gente de 0 a 780 s (13 min) y no hay desde los 810 s. | `plato_lleno_mucho` se abre a los 600 s, recordatorio a los 780 s (R = 3 min) y se resuelve a los 840 s (segunda lectura seguida con `people_present: false`; una sola no lo cierra). |
| `plato_vacio.jsonl` (T3, 21 líneas) | `T3-p1` está `comiendo` todo el tiempo. `T3-p2`: `lleno` 0–30 s, `comiendo` 60–90 s, `por_terminar` 120–150 s, `vacio` 180–420 s, y a los 450 s se lo llevan. La mesa nunca queda `terminada` (p1 sigue comiendo). | `plato_vacio` se abre a los 240 s (T1 = 60 s en `vacio`), recordatorio a los 420 s (R = 3 min) y se resuelve a los 450 s (retiraron ese plato). |
| `mesa_terminada_recordatorios.jsonl` (T4, 27 líneas) | `T4-p1` y `T4-p2` en `lleno` 0–60 s, `comiendo` 90–150 s y `vacio` de 180 a 720 s. Nadie los retira hasta los 750 s. | `mesa_terminada` se abre a los 180 s; recordatorios a los 360 s («terminó hace 3 min») y a los 540 s («hace 6 min»), y no un tercero a los 720 s (máximo 2); se resuelve a los 750 s con «Retiraron los platos 9 min 30 s después de que terminó la mesa». |
| `id_cambiado.jsonl` (T1, 12 líneas) | `T1-p1` (`comiendo`, `arriba-izquierda`) hasta 150 s. A los 180 s llegan **juntos** `T1-p1` (el suavizado lo retiene una lectura) y `T1-p4` en la misma posición, como en una corrida real; desde 210 s solo `T1-p4`. `T1-p2` (`abajo-derecha`) deja de venir a los 330 s (como con el suavizado, que lo retiene una lectura). | Ninguno. En `alerts/state.py`, `T1-p4` es provisional a los 180 s (no cuenta para las reglas) y a los 210 s hereda los tiempos de `T1-p1` (`first_seen` y `state_since` = 0 s). `T1-p2` se retira a los 330 s. |

## `expected/<escenario>.alerts.json`

Uno por escenario: los eventos que tiene que producir `alerts` al reproducirlo, en orden, con los mismos nombres que el log `vision/out/eventos.jsonl`:

```json
{ "at_seconds": 360, "event": "alert_opened", "type": "mesa_terminada", "table_id": "T1" }
```

`event`: `alert_opened` | `alert_reminder` | `alert_resolved`. `at_seconds` se cuenta desde la primera línea del `.jsonl`.
