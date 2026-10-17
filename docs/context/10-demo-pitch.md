# 10 — Demo y pitch

## Guion de demo (3 min)

1. **Problema (20 s)**: "El mozo no ve todas las mesas. Cada mesa que espera para pedir postre o la cuenta es plata y rotación perdida."
2. **En vivo (90 s)**: mesa armada con cámara arriba, dashboard proyectado, celular con el grupo de Telegram a la vista.
   - Plato lleno aparece → la mesa pasa a "servida".
   - Alguien "come" (retira comida) → "comiendo" → "por terminar" → llega el Telegram "Mesa 1 está terminando, ofrecé postre".
   - Se toca **Voy** en el celular → el dashboard muestra quién fue y el tiempo de respuesta.
   - Bonus: un plato que queda lleno → alerta "¿está todo bien?".
3. **Métricas (30 s)**: KPIs del dashboard: tiempo de comida, tiempo de respuesta, platos devueltos llenos.
4. **Negocio y roadmap (40 s)**: precio por mesa/mes, se instala con cámaras que el local ya tiene; próximos pasos: integración con POS, multi-cámara, detección de "mano levantada", WhatsApp Business.

## Planes B

| Si falla… | Hacemos… |
|---|---|
| La cámara en vivo | `vision/main.py --source videos/demo.mp4` |
| La visión entera | Simulador del backend con el mismo escenario |
| Internet | Video grabado de la demo completa |
| Telegram | Mostrar el feed de alertas del dashboard |

## Checklist pre-demo

- [ ] `DEMO_MODE=true` (umbrales cortos).
- [ ] Base de datos limpia (script de reset).
- [ ] Celular con Telegram abierto, brillo al máximo, no molestar activado.
- [ ] Laptop de visión enchufada; cámara fija y enfocada.
- [ ] Video de respaldo a mano.
