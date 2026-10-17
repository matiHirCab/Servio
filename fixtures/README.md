# fixtures/

Datos falsos fijos con la forma exacta de [03-contratos.md](../docs/context/03-contratos.md) (lista de archivos en T0-2 de [11-tareas.md](../docs/context/11-tareas.md)). Sirven para desarrollar y testear sin cámara ni modelo: tests de reglas, `web/scripts/simulate.ts`, clasificador `fake` en modo `replay` y mock del dashboard. Los validan `web/lib/contracts.test.ts` y `vision/tests/test_contracts.py`.

Dueños: `observations/` y `expected/` P2, `api/` P3 (ver [09-convenciones.md](../docs/context/09-convenciones.md)). Si cambiás un contrato, actualizá los fixtures en el mismo PR.

## `observations/*.jsonl`

Una observación completa de `POST /api/observations` por línea, una lectura cada 10 s desde `2026-10-17T20:00:00Z`. Los estados ya vienen suavizados. En la tabla, los segundos se cuentan desde la primera línea y los resultados usan los umbrales de **producción** de [05-reglas-alertas.md](../docs/context/05-reglas-alertas.md).

| Archivo | Qué pasa en las lecturas | Resultado esperado |
|---|---|---|
| `mesa_completa.jsonl` (T1, 55 líneas) | 0–20 s mesa vacía sin gente. 30–50 s llega gente, sin platos. 60–90 s `T1-p1` y `T1-p2` en `lleno`. 100–120 s `comiendo`. 130–170 s `por_terminar`. 180–300 s `vacio`. 310–480 s se retiran los platos y la gente sigue (3 min). 490–540 s llega un plato nuevo, `T1-p3`, en `lleno`. | `tableStatus()`: `libre → servida → comiendo → por_terminar → terminada → libre → servida`. Alertas en [`expected/mesa_completa.alerts.json`](expected/mesa_completa.alerts.json): `mesa_por_terminar` a los 130 s, que expira a los 180 s (la mesa pasa a `terminada`), y `plato_vacio` a los 240 s. `T1-p1` y `T1-p2` quedan con `removed_at` a los 320 s y ahí `plato_vacio` expira. |
| `plato_lleno_mucho.jsonl` (T2, 79 líneas) | `T2-p1` en `lleno` todo el tiempo, con `people_present: true` de 0 a 720 s (12 min) y `false` de 730 a 780 s. | En [`expected/plato_lleno_mucho.alerts.json`](expected/plato_lleno_mucho.alerts.json): `plato_lleno_mucho` a los 600 s (T2 = 10 min desde `first_seen`), una sola vez (cooldown). A los 730 s la condición ya no se cumple y expira. |
| `id_cambiado.jsonl` (T1, 12 líneas) | `T1-p1` (`comiendo`, `arriba-izquierda`) hasta 50 s; desde 60 s aparece como `T1-p4` en la misma posición. `T1-p2` (`abajo-derecha`) falta desde 100 s. | La reconciliación conserva `T1-p1` con `first_seen` y `state_since` = 0 s y guarda el alias `T1-p4 → T1-p1`: `id_reconciliations` = 1 aunque `T1-p4` siga llegando. `T1-p2` queda con `removed_at` a los 110 s (2 lecturas ausente). Ninguna alerta ([`expected/id_cambiado.alerts.json`](expected/id_cambiado.alerts.json) está vacío). |

## `expected/*.alerts.json`

Una por escenario: alertas que tiene que producir el motor al reproducirlo, `{ at_seconds, type, table_id, expired_at_seconds? }`. Los segundos se cuentan desde la primera línea; `expired_at_seconds` es cuándo pasa a `expirada` (si no se resuelve antes). Los tests del motor comparan contra estos archivos.

## `api/*.json`

Respuestas de ejemplo de `GET /api/tables`, `/api/alerts` y `/api/metrics`. Las arma P3 (T0-2).
