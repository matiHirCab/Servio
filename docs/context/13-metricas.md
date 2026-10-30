# 13 — Métricas para vender: cuáles, cómo se calculan, quién las ve

Lista cerrada de la tarea M-1. Va antes del diseño del dashboard (F-2): D-1 y D-4 muestran exactamente esto. Qué rol ve cada una sale de [12-roles.md](12-roles.md) y de `vision/config/servio.toml`; acá no se repite la config, solo se dice el default.

Las métricas son de tres tipos:

1. **Internas de operación:** lo que pasa en el salón, sale de la base (`servio.db`). Las ve el encargado y el dueño. Son las que venden.
2. **Externas:** números que no salen del salón sino de afuera (reseñas, mercado) o que se muestran hacia afuera (al prospecto, en la web, en un caso de éxito). Sirven para vender antes de tener un cliente y para que el dueño vea si sus clientes lo notan.
3. **Nuestras:** calidad y costo de Servio. No se le muestran al cliente (salvo el costo, que ve el dueño).

Las pantallas son una propuesta para F-2: **En vivo** (mesas y avisos ahora), **Turno** (cómo va el servicio de hoy), **Carta** (platos), **Resultados** (plata, histórico, antes/después) y **Configuración**.

## 1. Internas de operación

Umbrales en `servio_vision/metrics.py`: `UNATTENDED_LIMIT_MIN = 5`, `FORGOTTEN_LIMIT_S = 120`, almuerzo antes de las 17 h (`LUNCH_UNTIL_HOUR`) y `VISIT_GAP_MIN = 120`. Las horas se cuentan en hora de Uruguay (`LOCAL_TZ`, UTC−3): la cámara guarda en UTC y el historial ya está en hora local.

| id | Qué responde | Cómo se calcula | De qué tablas | Rol (default) | Pantalla | Estado |
|---|---|---|---|---|---|---|
| `avisos_abiertos` | ¿Qué mesas necesitan a alguien ahora y quién lo tomó? | Avisos con `resuelto_at` vacío, con minutos abiertos y la última respuesta del mozo ("Voy"). | `avisos`, `respuestas` | Encargado, Dueño | En vivo | Falta la consulta; "Voy"/"Listo" llega con C-5. |
| `tiempo_respuesta` | ¿Cuánto tarda cada mozo en reaccionar a un aviso? | Promedio de `respuestas.segundos` (de que salta el aviso a que responde), por mozo y por servicio. Con C-5 pasa a ser hasta el "Voy". | `respuestas`, `mozos` | Encargado, Dueño | Turno | **Se calcula** (`SQL_ANSWER`). |
| `mesa_terminada_sin_atender` | Cuando una mesa termina, ¿cuánto espera hasta que le retiran los platos? | Minutos `resuelto_at − abierto_at` de avisos `mesa_terminada` cerrados por `retirados`. Promedio, peor y **% que esperó más de 5 min**; por mozo en `SQL_ATTEND`. | `avisos`, `mozos` | Encargado, Dueño | Turno | **Se calcula** (`SQL_UNATTENDED`). |
| `mesas_olvidadas` | ¿Cuántas veces un plato quedó lleno y nadie fue? | Avisos `plato_lleno_mucho` sin respuesta o con la primera respuesta después de 2 min, sobre el total, por mozo. Un aviso abierto todavía sin respuesta no cuenta. | `avisos`, `respuestas`, `mozos` | Encargado, Dueño | Turno | **Se calcula** (`SQL_FORGOTTEN`). |
| `rotacion` | ¿Cuánto dura una mesa y cuántas mesas atiende el salón por servicio? | Visita = del primer plato servido después del último retiro hasta el retiro de la mesa terminada. Si la mesa pasa más de 2 h sin novedades, la visita abierta se descarta. Así, una mesa que nadie retiró no se cierra con el retiro de otra visita horas después. Minutos promedio por visita y visitas por servicio (almuerzo o cena de un día), en total y por tipo. Cada minuto que baja es capacidad para otra mesa. | `platos_servidos`, `avisos` | Encargado, Dueño | Turno | **Se calcula** (`rotation()`). No cuenta el tiempo desde que se sientan hasta el primer plato (no miramos personas). |
| `resumen_cierre` | ¿Cómo fue el servicio? | Las de esta tabla para un solo servicio, en un mensaje al cerrar. | las de arriba | Encargado, Dueño | Telegram / Turno | D-3. |
| `platos_que_vuelven` | ¿Qué platos vuelven con comida y por qué? | Platos servidos con aviso `plato_lleno_mucho` / platos servidos, por plato de la carta, con el motivo más respondido. | `platos`, `platos_servidos`, `avisos`, `respuestas` | Encargado, Dueño | Carta | **Se calcula** (`SQL_DISHES`, `SQL_PROBLEMS`). D-4 agrupa los textos libres. |
| `desperdicio` | ¿Cuánta plata vuelve a la cocina? | Suma del precio de la carta de los platos que volvieron con un problema (el mozo respondió algo distinto de "Está todo bien…"). Total y por servicio, por plato. Es una cota alta: cuenta el plato entero aunque vuelva a medias. | `platos.precio`, `platos_servidos`, `avisos`, `respuestas` | Dueño | Carta / Resultados | **Se calcula** (`SQL_WASTE`). Precio nuevo en `carta.json` (`precio`, en pesos). |
| `historico` | ¿Mejora o empeora? | Las de esta tabla por semana, más de 7 días. | todas | Dueño | Resultados | D-1 / D-4. |
| `antes_despues` | ¿Cuánto mejoró con Servio? | Las mismas métricas en modo sombra (D-5, sin avisos) y con avisos, misma cantidad de servicios. Titular: «con Servio tus mesas esperan X min menos». | todas + marca de sombra | Dueño | Resultados | M-2 (necesita D-5). |
| `costos_y_configuracion` | ¿Cuánto me cuesta Servio? | Costo del modelo por mesa por hora (sección 3) × horas abiertas + abono. | log de llamadas | Dueño | Configuración | C-2 / B-5. |

Con el historial simulado (`fixtures/restaurante/historial.jsonl`, 30 días, 4 mesas) `python tools/metricas.py` da: mesa terminada 4,1 min en promedio y 26 % más de 5 min; Martín olvida 16 de 42 avisos (38 %), el resto 0; 17,8 mesas por servicio; desperdicio $71.360 en 30 días ($1.189 por servicio), la mitad por el risotto. El tiempo de mesa (13 min) es irreal porque el simulador avanza 4 min por evento: no usarlo en el pitch.

### Sumadas el 29/10

Tomás pidió sumar todas las propuestas internas. Ya están en el catálogo (`METRICS` de `roles.py`) y en `servio.toml`.

| id | Qué responde | Cómo se calcula | De qué tablas | Rol (default) | Pantalla | Estado |
|---|---|---|---|---|---|---|
| `carga_por_mozo` | ¿El reparto de mesas es parejo? | Por mozo: visitas (ver `rotacion`) / servicios en que trabajó, y minutos promedio hasta retirar una mesa terminada. El que tiene más mesas y peores tiempos necesita que le saquen una mesa. | `platos_servidos`, `avisos`, `mesas`, `mozos` | Encargado, Dueño | Turno | **Se calcula** (`waiter_load()`). |
| `horas_pico` | ¿A qué hora se cae la atención? | Por franja de 30 min (`PEAK_SLOT_MIN`): avisos abiertos y % de mesas terminadas que esperaron más de 5 min. Sirve para decidir cuántos mozos poner por turno. | `avisos` | Encargado, Dueño | Turno | **Se calcula** (`peak_hours()`). |
| `espera_siguiente_plato` | ¿Una mesa quedó sin nada esperando el siguiente plato? | Minutos de los avisos `mesa_terminada` que se cerraron porque llegó otro plato (`motivo_cierre = 'servido'`), por mozo. | `avisos`, `mozos` | Encargado, Dueño | Turno | **Se calcula** (`SQL_NEXT_DISH`). Solo con datos en vivo: el historial simulado no tiene estos casos. |
| `ritmo_por_plato` | ¿Qué platos se comen lento? | Minutos desde que se sirvió hasta la primera lectura en `vacio`, promedio por plato de la carta. Lento + vuelve lleno = porción grande o no gusta. | `estados_plato` (nueva), `platos_servidos`, `platos` | Dueño | Carta | **Se calcula** (`SQL_PACE`). Solo con datos en vivo. |
| `sobra_por_plato` | ¿Cuánto queda en el plato cuando se lo llevan? | Último `fill_ratio` de los platos que se fueron de la mesa (el loop marca `retirado_at` en la lectura en que dejan de estar; si vuelven a verse, se borra), promedio por plato. Más adelante puede afinar el desperdicio en $ (precio × lo que sobra, en vez del plato entero). | `platos_servidos.ultimo_fill_ratio` y `retirado_at` (nuevas) | Dueño | Carta | **Se calcula** (`SQL_LEFTOVER`). Solo con datos en vivo. |
| `adopcion` | ¿Los mozos usan Servio? | Avisos de plato lleno ya cerrados que tuvieron respuesta / total, y cuántos se marcaron "No hacía falta". Si baja, el local deja de usar Servio: para nosotros avisa que el cliente se puede ir. | `avisos`, `respuestas` | Dueño | Resultados | **Se calcula** (`SQL_ADOPTION`). Con C-5 cuenta todos los avisos y el botón "No hacía falta". |

Datos nuevos que guarda el loop (`DatabaseOutput` en `alerts/outputs.py`), en una transacción por lectura de mesa:
- En `platos_servidos`, el último `fill_ratio` del plato. Si la lectura no lo trae queda vacío, para no arrastrar uno viejo.
- También en `platos_servidos`, `retirado_at`: la lectura en que el plato se fue de la mesa.
- Una fila en `estados_plato` cada vez que el plato cambia de estado.

Si el tracker le cambia el id a un plato (hereda los tiempos del anterior), sigue siendo el mismo plato servido: no cuenta como retirado ni como plato nuevo.

## 2. Externas

| Métrica | Qué responde | Cómo se calcula | De dónde sale | Quién la ve | Estado |
|---|---|---|---|---|---|
| Quejas de servicio en reseñas de Montevideo | ¿Este problema existe? | Reseñas que se quejan de demora, mozo que no viene, ser ignorados o trato / reseñas leídas. 18,5 % de 297; **57 % de las de 1–2★**; servicio o plato en 80 % de las de 1–2★; demora sola 7,7 %. | `/mnt/project-files/servio-resenas/` (TripAdvisor, 14 restaurantes, clasificadas por un modelo) | Pitch, web, prospecto | **Hecho** (18/10). Antes de usarlo en ventas, revisar a mano las 55 quejas. |
| Quejas en las reseñas del propio local | ¿Mis clientes lo notan? | Lo mismo, solo con las reseñas del local, 3 meses antes vs. después de instalar Servio. | Google Maps / TripAdvisor del cliente | Dueño (Resultados) | Falta: bajar las reseñas de un local (Google bloqueado desde la nube; a mano o con API de Places). |
| Puntaje promedio del local | ¿Subió la nota? | Promedio de estrellas por mes, antes vs. después. | Igual que la anterior | Dueño | Igual. Tarda meses en moverse: usarlo como apoyo, no como promesa. |
| Plata recuperada (ROI) | ¿Me conviene pagarlo? | (mesas más por servicio × ticket promedio × servicios por mes) + desperdicio evitado − costo de Servio. Las mesas más salen de `antes_despues` de `rotacion`. | `antes_despues` + ticket promedio que carga el dueño | Dueño y prospecto (con datos de un piloto) | Falta: ticket promedio en la config y M-2. |
| Comparación con otros locales | ¿Estoy mejor o peor que el resto? | Mediana de `mesa_terminada_sin_atender` y `mesas_olvidadas` de todos los locales con Servio, anónima, vs. la del local. | Base central con `local_id` | Dueño | Fase E (servidor central). Necesita 3+ locales. |
| Caso de éxito | La frase para la web | El titular de `antes_despues` de un piloto, con permiso del local. | M-2 | Público | Después del primer piloto. |

## 3. Nuestras (no se muestran al cliente)

| Métrica | Cómo se calcula | De dónde | Para qué |
|---|---|---|---|
| Acierto por estado | Estados bien / total en el set etiquetado, por estado. | `tools/eval.py`, B-3 | Saber si un cambio de prompt empeora. |
| Avisos falsos por hora | Avisos marcados "No hacía falta" / horas abiertas. | `respuestas` (C-5), escenarios de B-2 | Es lo que hace que el mozo deje de mirar el reloj. |
| Latencia del aviso | Segundos desde el frame que dispara el aviso hasta que llega a Telegram. | log del loop | Un aviso tarde no sirve. |
| Costo por mesa por hora | Llamadas al modelo × costo por llamada / (mesas × horas). | log de llamadas (C-2) | Define el precio mínimo. El dueño lo ve en `costos_y_configuracion`. |
| Salud | % del servicio con cámara, modelo y conexión andando. | latido de C-4 | Un local sin Servio la mitad de la noche no mide nada. |

Todas juntas van en el tablero de B-5 (`servio eval --all`).

## Cómo verlas

```
python tools/metricas.py                 # todas
python tools/metricas.py --rol encargado # sin desperdicio
python tools/metricas.py --sql           # con las consultas
```

## Pendiente de decidir

- Si los umbrales (5 min, 2 min) van a `servio.toml` para que cada local los cambie. Hoy están en el código.
- Pantalla final de cada métrica: la propuesta de arriba se confirma con F-2.
