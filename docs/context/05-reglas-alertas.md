# 05 — Reglas de alertas

Viven en el backend (`web/lib/rules/`). Se evalúan cada vez que llega una observación. Todos los umbrales en `web/lib/rules/config.ts` para poder cambiarlos rápido en la demo (en la demo usar tiempos cortos).

| Regla | Dispara cuando | Mensaje al mozo | Producción | Demo |
|---|---|---|---|---|
| `mesa_por_terminar` | ≥ 50 % de los platos de la mesa en `por_terminar` o `vacio`, y al menos uno en `por_terminar` | "🍽️ Mesa 3 está terminando. Ofrecé postre o café." | — | — |
| `plato_vacio` | Un plato en `vacio` hace ≥ T1 | "🧹 Mesa 3: hay platos vacíos para retirar." | T1 = 60 s | 10 s |
| `plato_lleno_mucho` | Un plato en `lleno` hace ≥ T2 desde `first_seen` | "❓ Mesa 3: un plato sigue lleno hace 12 min. Preguntá si está todo bien." | T2 = 10 min | 45 s |
| `bebida_vacia` | Una bebida en `vacia` hace ≥ T3 | "🥤 Mesa 3: bebida vacía, ofrecé otra." | T3 = 60 s | 10 s |

## Antispam

- **Cooldown** por `(mesa, tipo)`: no repetir la misma alerta si hay una `abierta`/`en_camino` o si se resolvió hace < 5 min (demo: 30 s).
- **Agrupar**: si en la misma evaluación disparan `plato_vacio` y `mesa_por_terminar`, mandar un solo mensaje con ambas cosas.
- **Expirar**: si la condición deja de cumplirse (retiraron el plato), la alerta pasa a `expirada` sola y se edita el mensaje de Telegram ("✅ ya resuelto").

## Estado de mesa (para el dashboard)

Derivado de sus platos:
- sin ítems → `libre`
- todos `vacio` → `terminada`
- mayoría `por_terminar`/`vacio` → `por_terminar`
- mayoría `lleno` → `servida`
- resto → `comiendo`

## Tests

Las reglas son funciones puras `(estadoMesa, ahora, config) → Alerta[]`. Escribir tests con tiempos simulados (Vitest). Es lo más fácil de testear y lo que más se rompe.
