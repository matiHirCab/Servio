# 12 — Roles y permisos: mozo, encargado y dueño

Quién ve qué en la vista de reconocimiento, el dashboard y los avisos (tarea R-1). Decidido por Tomás el 28/10. Va antes del diseño del front (F-1, F-2, F-3).

## Qué ve cada rol

**Mozo**
- En la pantalla ve la vista de reconocimiento (F-1) con **todas** las mesas.
- En el **reloj** le llegan los avisos **solo de sus mesas**, y responde «Voy», «Listo» o el motivo. El mozo de cada mesa va en `servio.toml` con C-7.
- No ve métricas ni dashboard.

**Encargado:** vista de reconocimiento + dashboard con las métricas del default de abajo.

**Dueño:** admin. Ve todo y es el único que entra a la configuración, donde puede cambiar qué métricas ve cada rol.

## Métricas por rol (default)

Qué es cada métrica y cómo se calcula: [13-metricas.md](13-metricas.md).

| Métrica (M-1) | id en la config | Mozo | Encargado | Dueño |
|---|---|---|---|---|
| Avisos abiertos y quién los tomó | `avisos_abiertos` | – | Sí | Sí |
| Tiempo de respuesta por mozo (turno y día) | `tiempo_respuesta` | – | Sí | Sí |
| Mesa terminada sin atender | `mesa_terminada_sin_atender` | – | Sí | Sí |
| Mesas olvidadas (lleno mucho rato) | `mesas_olvidadas` | – | Sí | Sí |
| Tiempo de mesa y rotación del día | `rotacion` | – | Sí | Sí |
| Resumen al cerrar el servicio | `resumen_cierre` | – | Sí | Sí |
| Platos que vuelven con comida (% y motivo) | `platos_que_vuelven` | – | Sí | Sí |
| Desperdicio en $ | `desperdicio` | – | No | Sí |
| Histórico y tendencias (más de 7 días) | `historico` | – | No | Sí |
| Antes vs. después con Servio | `antes_despues` | – | No | Sí |
| Costo de Servio, configuración, usuarios y roles | `costos_y_configuracion` | – | No | Sí |
| Carga por mozo (mesas por servicio y tiempos) | `carga_por_mozo` | – | Sí | Sí |
| Horas pico (avisos y demoras por franja) | `horas_pico` | – | Sí | Sí |
| Espera del siguiente plato | `espera_siguiente_plato` | – | Sí | Sí |
| Ritmo por plato (minutos hasta vacío) | `ritmo_por_plato` | – | No | Sí |
| Sobra por plato (cuánto queda al retirarlo) | `sobra_por_plato` | – | No | Sí |
| Adopción de los mozos (avisos respondidos) | `adopcion` | – | No | Sí |

Criterio: el encargado ve todo lo que le sirve para operar el turno; el dueño además ve plata, historia y configuración.

## Dónde se guarda

En la sección `[roles]` de [`vision/config/servio.toml`](../../vision/config/servio.toml), no en el código. Cada rol tiene:

| Campo | Valores | Qué define |
|---|---|---|
| `vistas` | `reconocimiento`, `dashboard`, `configuracion` | Pantallas que puede abrir. |
| `mesas` | `todas`, `suyas` | Qué mesas ve en la vista de reconocimiento. |
| `avisos` | `suyas`, `todas`, `ninguno` | Qué avisos le llegan al reloj. |
| `metricas` | ids de la tabla de arriba | Qué métricas ve en el dashboard. |
| `admin` | `true` / `false` | Con `true` ve todas las vistas y métricas, aunque no estén en las listas. |

Para que un rol vea otra métrica se agrega su id a su lista. Un id mal escrito hace fallar la carga con un error que dice cuál es.

`servio_vision/roles.py` la lee: `Roles.load().can_see("encargado", "desperdicio")`, `can_open(rol, vista)`. El catálogo de métricas (`METRICS`) sí está en el código, porque cada métrica necesita código para calcularse.

## Quién la usa

- `tools/metricas.py --rol encargado` muestra solo lo que ve ese rol (con `--rol mozo` no muestra nada).
- El dashboard (D-1) pregunta con `can_see` / `can_open` antes de mostrar cada métrica o pantalla.
- La pantalla para que el dueño cambie la tabla va con la configuración del local desde la web (D-2), y escribe este mismo archivo.

## Pendiente

- El comando `/metricas` del bot manda las métricas al grupo de Telegram, donde están los mozos. Con estos roles el mozo no debería verlas: cuando cada rol tenga su chat (C-7), `/metricas` se responde solo a encargado y dueño.
- Avisos para encargado y dueño: por ahora `ninguno` (solo el mozo los recibe en el reloj).
