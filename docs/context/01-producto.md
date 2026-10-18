# 01 — Producto

## Problema

En un restaurante el mozo no puede mirar todas las mesas todo el tiempo. Pasan dos cosas caras:

- **Mesas que terminaron y nadie retira** → el cliente espera para pedir postre, café o la cuenta. Se pierde venta y rotación.
- **Platos que siguen llenos mucho tiempo** → algo anda mal (no le gustó, está frío, se equivocaron de pedido) y nadie se entera hasta la reseña.

## Solución

Una cámara en alto mira el salón. Servio:

1. Encuentra cada **plato de comida** sobre cada mesa (YOLO).
2. Estima cuánta comida queda en cada plato (Claude) y le asigna un estado:

   | Estado | Comida que queda | ¿Avisa? |
   |---|---|---|
   | `lleno` | ≥ 75 % | Sí, si sigue así mucho rato |
   | `comiendo` | 30–75 % | No |
   | `por_terminar` | 5–30 % | No (solo el color de la mesa) |
   | `vacio` | < 5 % | Sí |

3. Le manda un **mensaje al mozo por Telegram** cuando tiene que ir a una mesa. Si un plato sigue lleno, el mozo responde con un botón ("Muy dulces", "Comanda equivocada"…) o escribe; la respuesta queda en la base para las métricas.

## Usuario

- **Mozo**: recibe los avisos en el grupo de Telegram "Mozos" en su celular. No instala nada ni toca nada.

## Avisos del MVP

| Aviso | Cuándo | Para qué |
|---|---|---|
| **Mesa terminó** | Todos los platos de la mesa están `vacio` | Ir a ofrecer postre, café o la cuenta |
| **Plato vacío** | Un plato está `vacio` hace un rato y el resto de la mesa sigue comiendo | Ir a retirar |
| **Lleno mucho rato** | Un plato sigue `lleno` mucho tiempo con gente en la mesa | Preguntar si está todo bien (no le gustó) |

Umbrales y antispam en [05-reglas-alertas.md](05-reglas-alertas.md).

## Alcance del MVP (24 h)

**Sí:**
- Un video con varias mesas vistas desde arriba (grabado o en vivo desde un celular).
- Mesas detectadas solas (YOLO de COCO), numeradas por filas; zonas a mano solo si se pasa `--zones`.
- YOLO para encontrar platos + Claude para estimar cuánta comida queda + suavizado en el tiempo.
- Los tres avisos por Telegram, con recordatorio si la situación sigue y "✅ ya resuelto" cuando se resuelve.

**No (roadmap para el pitch):**
- Bebidas.
- Respuesta del mozo (botones "Voy/Listo") y métricas de tiempo de respuesta por mozo.
- Dashboard y métricas para el dueño.
- Aviso de "esperando el siguiente plato" (tiempo entre platos).
- Integración con POS, multi-cámara, detección de gestos ("levantó la mano").
