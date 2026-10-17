# 05 — Reglas de alertas

Viven en el backend (`web/lib/rules/`). Se evalúan **solo** cuando llega una observación (`POST /api/observations`), nunca en los GET: así varias pestañas del dashboard no generan alertas duplicadas. El refresco forzado de la visión (60 s / demo 20 s, ver [04-vision.md](04-vision.md)) asegura que lleguen observaciones seguido aunque la mesa no cambie. Todos los umbrales en `web/lib/rules/config.ts` para poder cambiarlos rápido en la demo (en la demo usar tiempos cortos).

| Regla | Dispara cuando | Mensaje al mozo | Producción | Demo |
|---|---|---|---|---|
| `mesa_por_terminar` | `tableStatus()` de la mesa **pasa a** `por_terminar` (ver «Estado de mesa») | "🍽️ Mesa 3 está terminando. Ofrecé postre o café." | — | — |
| `plato_vacio` | Un plato en `vacio` hace ≥ T1 | "🧹 Mesa 3: hay platos vacíos para retirar." | T1 = 60 s | 10 s |
| `plato_lleno_mucho` | Un plato en `lleno` hace ≥ T2 desde `first_seen` **y** `people_present` no es `false` | "❓ Mesa 3: un plato sigue lleno hace 12 min. Preguntá si está todo bien." | T2 = 10 min | 45 s |
| `esperando_plato` | Mesa con `people_present = true`, **sin platos**, hace ≥ T4 desde que se retiraron los anteriores (solo si ya tuvo platos) | "⏳ Mesa 3: hace 8 min que no tiene platos. Preguntá si quieren pedir algo más." | T4 = 8 min | 30 s |

## Umbrales y cadencia de lectura

Las lecturas llegan cada 15–30 s (demo: 5–10 s). Como el suavizado exige 2 lecturas seguidas para aceptar un cambio de estado, el tiempo mínimo observable es ≈ 2 × cadencia. **Regla:** todo umbral de demo debe ser ≥ 2 × la cadencia configurada (con lecturas cada 5 s, T1 = 10 s es el mínimo viable). `config.ts` valida esto al arrancar y avisa si no se cumple.

## `people_present`

- Si `people_present = false` no se dispara `plato_lleno_mucho` (no tiene sentido preguntar si está todo bien a una mesa sin gente).
- Si `people_present = false` y hay platos en `vacio`, `plato_vacio` sigue disparando (hay que retirar).
- Si `people_present = false` no se dispara `esperando_plato` (la mesa se fue).

## Antispam

- **Cooldown** por `(mesa, tipo)`: no repetir la misma alerta si hay una `abierta`/`en_camino` o si se resolvió hace < 5 min (demo: 30 s).
- **Agrupar**: si en la misma evaluación disparan `plato_vacio` y `mesa_por_terminar`, mandar un solo mensaje con ambas cosas.
- **Expirar**: si la condición deja de cumplirse (retiraron el plato, o en `esperando_plato` llegó un plato nuevo), la alerta pasa a `expirada` sola y se edita el mensaje de Telegram ("✅ ya resuelto"). Para `mesa_por_terminar`, expira solo cuando `tableStatus()` pasa a `terminada` o `libre`. Si la mesa vuelve a `comiendo` (llegó otro plato), la alerta sigue abierta y el cooldown evita repetirla en segundos; y como expira al terminar, no bloquea la alerta del plato siguiente. Un plato cuenta como retirado recién cuando tiene `removed_at` (2 lecturas ausente, regla 3 de la reconciliación en [03-contratos.md](03-contratos.md)): con 1 lectura ausente se mantiene y su alerta no expira.

## Estado de mesa (para el dashboard)

Función pura única `tableStatus(items)` en `web/lib/rules/`, usada por el motor de reglas **y** por `GET /api/tables`. Los ítems son siempre platos (las bebidas están fuera de alcance). Se evalúa en este orden:

1. sin platos → `libre`
2. todos `vacio` → `terminada`
3. ≥ 50 % de los platos en `por_terminar` o `vacio` → `por_terminar`
4. ≥ 50 % de los platos en `lleno` → `servida`
5. resto → `comiendo`

Así el color de la mesa en el dashboard y la alerta `mesa_por_terminar` nunca se contradicen.

## Tests

El motor es una función pura `(estadoMesa, ahora, config, alertasAbiertas) → { nuevas: Alerta[], expiradas: Alerta[] }`. `nuevas` respeta el cooldown y el agrupado; `expiradas` son las abiertas cuya condición ya no se cumple. `POST /api/observations` guarda las nuevas y llama a `Notifier.send`, y pasa las expiradas a `expirada` y llama a `Notifier.update` (ver [03-contratos.md](03-contratos.md)). Los tests reproducen `fixtures/observations/*.jsonl` y comparan contra `fixtures/expected/`. Escribir tests con tiempos simulados (Vitest). Es lo más fácil de testear y lo que más se rompe.
