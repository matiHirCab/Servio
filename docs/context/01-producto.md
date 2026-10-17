# 01 — Producto

## Problema

En un restaurante el mozo no puede mirar todas las mesas todo el tiempo. Pasan dos cosas caras:

- **Mesas que terminaron y nadie retira** → el cliente espera para pedir postre, café o la cuenta. Se pierde venta y rotación.
- **Platos que siguen llenos mucho tiempo** → algo anda mal (no le gustó, está frío, se equivocaron de pedido) y nadie se entera hasta la reseña.

## Solución

Una cámara cenital (en el techo) mira el salón. Servio:

1. Detecta cada **plato de comida** sobre cada mesa (las bebidas quedan fuera del MVP).
2. Clasifica su estado:

   | Estado | Significado |
   |---|---|
   | `lleno` | Recién servido, casi intacto (> 80 % de comida). |
   | `comiendo` | En consumo (30–80 %). No dispara ningún aviso. |
   | `por_terminar` | Queda poco (5–30 %). |
   | `vacio` | Sin comida o solo restos (< 5 %). |

3. Le manda un **mensaje al mozo** por Telegram cuando hay que actuar.
4. Guarda todo y muestra **métricas** al dueño/encargado en un dashboard.

## Usuarios

- **Mozo**: recibe avisos en el celular. Toca "Voy" / "Listo". No usa el dashboard.
- **Encargado / dueño**: mira el dashboard en vivo y las métricas del día.

## Alertas del MVP (prioridad)

1. **Mesa por terminar** — la mayoría de los platos de una mesa está en `por_terminar`. Ir a ofrecer postre/café.
2. **Plato vacío** — hay platos `vacio` hace más de N segundos. Ir a retirar.
3. **Plato lleno mucho tiempo** — un plato sigue `lleno` más de X minutos desde que apareció. Ir a preguntar si está todo bien.

El detalle de umbrales está en [05-reglas-alertas.md](05-reglas-alertas.md).

## Métricas para el negocio

- Tiempo medio de comida por mesa (servido → vacío).
- Tiempo de respuesta del mozo (alerta → "Listo").
- Alertas por mesa / por mozo / por hora.
- Platos que vuelven llenos (posible problema con un plato del menú).
- Ocupación y rotación de mesas.

## Alcance del MVP (24 h)

**Sí:**
- 1 cámara (webcam o celular) mirando desde arriba 1–4 mesas (en la demo, una mesa armada con platos reales).
- Zonas de mesa definidas a mano (polígonos en un JSON).
- Detección + clasificación de estado + suavizado temporal.
- Bot de Telegram con botones "Voy" / "Listo".
- Dashboard web con mapa de mesas en vivo, feed de alertas y 3–4 métricas.

**No (lo contamos en el pitch como roadmap):**
- Multi-cámara y calibración automática de mesas.
- Integración con POS / sistema de comandas.
- Identificar qué plato del menú es cada uno.
- Detección de personas / gestos ("levantó la mano").
- Bebidas (vasos, tazas, botellas) y la alerta de bebida vacía.
- WhatsApp u otros canales de aviso (solo Telegram).
- App nativa para mozos.
