# fixtures/

Observaciones de mesa de ejemplo y los avisos que tienen que salir, con el formato de [03-contratos.md](../docs/context/03-contratos.md). Sirven para probar las reglas y Telegram sin video ni modelo: tests del motor (MAT-3), `vision/tools/replay.py` (MAT-6) y plan B de la demo. Los valida `vision/tests/test_alert_models.py` contra `servio_vision/alerts/models.py`.

Dueño: Matías (ver [09-convenciones.md](../docs/context/09-convenciones.md)). Si cambiás el contrato, actualizá los fixtures en el mismo PR.

## `observations/*.jsonl`

Una observación de mesa por línea (`table_id` arriba), una lectura cada 10 s desde `2026-10-17T20:00:00Z`. Como es lo que manda el pipeline después de suavizar, solo hay platos visibles, cada `fill_ratio` coincide con su estado, y un plato que deja de aparecer ya se considera retirado. En la tabla, los segundos se cuentan desde la primera línea, con los umbrales de **producción** de [05-reglas-alertas.md](../docs/context/05-reglas-alertas.md) (T1 = 60 s, T2 = 10 min, R = 3 min).

| Archivo | Qué pasa | Avisos esperados |
|---|---|---|
| `mesa_completa.jsonl` (T1, 55 líneas) | 0–20 s mesa vacía sin gente. 30–50 s llega gente, sin platos. 60–90 s `T1-p1` y `T1-p2` en `lleno`. 100–120 s `comiendo`. 130–170 s `por_terminar`. 180–300 s `vacio`. A los 310 s se retiran y la gente sigue (3 min). 490–540 s plato nuevo `T1-p3` en `lleno`. | `table_status`: `libre → servida → comiendo → por_terminar → terminada → libre → servida`. `mesa_por_terminar` se abre a los 130 s y se resuelve a los 180 s (la mesa pasa a `terminada`). `plato_vacio` se abre a los 240 s y se resuelve a los 310 s (retiraron los platos). |
| `plato_lleno_mucho.jsonl` (T2, 85 líneas) | `T2-p1` en `lleno` todo el tiempo. Hay gente de 0 a 780 s (13 min) y no hay desde los 790 s. | `plato_lleno_mucho` se abre a los 600 s, recordatorio a los 780 s (R = 3 min) y se resuelve a los 790 s (`people_present: false`). |
| `id_cambiado.jsonl` (T1, 12 líneas) | `T1-p1` (`comiendo`, `arriba-izquierda`) hasta 50 s; desde 60 s aparece como `T1-p4` en la misma posición. `T1-p2` (`abajo-derecha`) deja de venir a los 100 s. | Ninguno. `alerts/state.py` trata `T1-p4` como `T1-p1` (no reinicia `first_seen` ni `state_since`). |

## `expected/<escenario>.alerts.json`

Uno por escenario: los eventos que tiene que producir `alerts` al reproducirlo, en orden, con los mismos nombres que el log `vision/out/eventos.jsonl`:

```json
{ "at_seconds": 130, "event": "alert_opened", "type": "mesa_por_terminar", "table_id": "T1" }
```

`event`: `alert_opened` | `alert_reminder` | `alert_resolved`. `at_seconds` se cuenta desde la primera línea del `.jsonl`.
