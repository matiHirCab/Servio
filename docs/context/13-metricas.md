# 13 — Métricas para vender: cuáles, cómo se calculan, quién las ve

Lista cerrada de la tarea M-1. Va antes del diseño del dashboard (F-2): D-1 y D-4 muestran exactamente esto. Qué rol ve cada una sale de [12-roles.md](12-roles.md) y de `vision/config/servio.toml`; acá no se repite la config, solo se dice el default.

Las métricas son de tres tipos:

1. **Internas de operación:** lo que pasa en el salón, sale de la base (`servio.db`). Las ve el encargado y el dueño. Son las que venden.
2. **Externas:** números que no salen del salón sino de afuera (reseñas, mercado) o que se muestran hacia afuera (al prospecto, en la web, en un caso de éxito). Sirven para vender antes de tener un cliente y para que el dueño vea si sus clientes lo notan.
3. **Nuestras:** calidad y costo de Servio. No se le muestran al cliente (salvo el costo, que ve el dueño).

Las pantallas son una propuesta para F-2: **En vivo** (mesas y avisos ahora), **Turno** (cómo va el servicio de hoy), **Carta** (platos), **Resultados** (plata, histórico, antes/después) y **Configuración**.

## 1. Internas de operación

Umbrales en `servio_vision/metrics.py`: `UNATTENDED_LIMIT_MIN = 5`, `FORGOTTEN_LIMIT_S = 120`, almuerzo antes de las 17 h (`LUNCH_UNTIL_HOUR`).

| id | Qué responde | Cómo se calcula | De qué tablas | Rol (default) | Pantalla | Estado |
|---|---|---|---|---|---|---|
| `avisos_abiertos` | ¿Qué mesas necesitan a alguien ahora y quién lo tomó? | Avisos con `resuelto_at` vacío, con minutos abiertos y la última respuesta del mozo ("Voy"). | `avisos`, `respuestas` | Encargado, Dueño | En vivo | Falta la consulta; "Voy"/"Listo" llega con C-5. |
| `tiempo_respuesta` | ¿Cuánto tarda cada mozo en reaccionar a un aviso? | Promedio de `respuestas.segundos` (de que salta el aviso a que responde), por mozo y por servicio. Con C-5 pasa a ser hasta el "Voy". | `respuestas`, `mozos` | Encargado, Dueño | Turno | **Se calcula** (`SQL_ANSWER`). |
| `mesa_terminada_sin_atender` | Cuando una mesa termina, ¿cuánto espera hasta que le retiran los platos? | Minutos `resuelto_at − abierto_at` de avisos `mesa_terminada` cerrados por `retirados`. Promedio, peor y **% que esperó más de 5 min**; por mozo en `SQL_ATTEND`. | `avisos`, `mozos` | Encargado, Dueño | Turno | **Se calcula** (`SQL_UNATTENDED`). |
| `mesas_olvidadas` | ¿Cuántas veces un plato quedó lleno y nadie fue? | Avisos `plato_lleno_mucho` sin respuesta o con la primera respuesta después de 2 min, sobre el total, por mozo. Un aviso abierto todavía sin respuesta no cuenta. | `avisos`, `respuestas`, `mozos` | Encargado, Dueño | Turno | **Se calcula** (`SQL_FORGOTTEN`). |
| `rotacion` | ¿Cuánto dura una mesa y cuántas mesas atiende el salón por servicio? | Visita = del primer plato servido después del último retiro hasta el retiro de la mesa terminada. Minutos promedio por visita y visitas por servicio (almuerzo o cena de un día), en total y por tipo. Cada minuto que baja es capacidad para otra mesa. | `platos_servidos`, `avisos` | Encargado, Dueño | Turno | **Se calcula** (`rotation()`). No cuenta el tiempo desde que se sientan hasta el primer plato (no miramos personas). |
| `resumen_cierre` | ¿Cómo fue el servicio? | Las de esta tabla para un solo servicio, en un mensaje al cerrar. | las de arriba | Encargado, Dueño | Telegram / Turno | D-3. |
| `platos_que_vuelven` | ¿Qué platos vuelven con comida y por qué? | Platos servidos con aviso `plato_lleno_mucho` / platos servidos, por plato de la carta, con el motivo más respondido. | `platos`, `platos_servidos`, `avisos`, `respuestas` | Encargado, Dueño | Carta | **Se calcula** (`SQL_DISHES`, `SQL_PROBLEMS`). D-4 agrupa los textos libres. |
| `desperdicio` | ¿Cuánta plata vuelve a la cocina? | Suma del precio de la carta de los platos que volvieron con un problema (el mozo respondió algo distinto de "Está todo bien…"). Total y por servicio, por plato. Es una cota alta: cuenta el plato entero aunque vuelva a medias. | `platos.precio`, `platos_servidos`, `avisos`, `respuestas` | Dueño | Carta / Resultados | **Se calcula** (`SQL_WASTE`). Precio nuevo en `carta.json` (`precio`, en pesos). |
| `historico` | ¿Mejora o empeora? | Las de esta tabla por semana, más de 7 días. | todas | Dueño | Resultados | D-1 / D-4. |
| `antes_despues` | ¿Cuánto mejoró con Servio? | Las mismas métricas en modo sombra (D-5, sin avisos) y con avisos, misma cantidad de servicios. Titular: «con Servio tus mesas esperan X min menos». | todas + marca de sombra | Dueño | Resultados | M-2 (necesita D-5). |
| `costos_y_configuracion` | ¿Cuánto me cuesta Servio? | Costo del modelo por mesa por hora (sección 3) × horas abiertas + abono. | log de llamadas | Dueño | Configuración | C-2 / B-5. |

Con el historial simulado (`fixtures/restaurante/historial.jsonl`, 30 días, 4 mesas) `python tools/metricas.py` da: mesa terminada 4,1 min en promedio y 26 % más de 5 min; Martín olvida 16 de 42 avisos (38 %), el resto 0; 17,8 mesas por servicio; desperdicio $71.360 en 30 días ($1.189 por servicio), la mitad por el risotto. El tiempo de mesa (13 min) es irreal porque el simulador avanza 4 min por evento: no usarlo en el pitch.

### Propuestas nuevas (no están en el catálogo todavía)

Para sumarlas hay que agregar su id a `METRICS` en `roles.py` y al rol en `servio.toml`.

| id propuesto | Qué responde | Cómo se calcularía | Rol propuesto | Qué falta |
|---|---|---|---|---|
| `carga_por_mozo` | ¿El reparto de mesas es parejo? | Visitas, avisos y minutos de mesa terminada por mozo y servicio. Mozo con más carga y peores tiempos = reasignar mesas. | Encargado, Dueño | Nada: sale de `table_visits()` + `mesas.mozo_id`. |
| `horas_pico` | ¿A qué hora se cae la atención? | Avisos y % de mesa terminada fuera de tiempo por franja de 30 min. Sirve para decidir cuántos mozos poner por turno. | Encargado, Dueño | Nada: agrupar por hora de `abierto_at`. |
| `ritmo_por_plato` | ¿Qué platos se comen lento? | Minutos de `lleno` a `vacio` por plato de la carta. Lento + vuelve lleno = porción grande o no gusta. | Dueño | Guardar los cambios de estado del plato en la base (hoy solo quedan los avisos). |
| `sobra_por_plato` | ¿Cuánto queda en el plato cuando se retira? | Último `fill_ratio` antes de que el plato desaparezca, promedio por plato. Afina el desperdicio en $ (plato × lo que sobra, no plato entero). | Dueño | Guardar el último `fill_ratio` en `platos_servidos`. |
| `adopcion` | ¿Los mozos usan Servio? | % de avisos con respuesta y % marcados "No hacía falta". Si baja, el cliente se va (señal de churn para nosotros). | Dueño | C-5. |
| `espera_siguiente_plato` | ¿Una mesa quedó sin nada en la mesa esperando el principal? | Minutos entre el retiro del plato anterior y el siguiente `servido` en la misma visita. | Encargado, Dueño | D-7. |

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
- Qué propuestas nuevas entran al catálogo.
- Pantalla final de cada métrica: la propuesta de arriba se confirma con F-2.
