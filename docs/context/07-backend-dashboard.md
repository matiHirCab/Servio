# 07 — Backend y dashboard

## Stack

- **Next.js (App Router) + TypeScript** en `web/`, deploy en Vercel.
- **Postgres** desde el marketplace de Vercel (Neon). ORM: **Drizzle** (liviano) o Prisma.
- UI: **Tailwind + shadcn/ui**. Gráficos: Recharts.
- Tests: **Vitest** para reglas.

## API (ver contratos en [03-contratos.md](03-contratos.md))

| Ruta | Qué hace |
|---|---|
| `POST /api/observations` | Recibe la visión, actualiza `items`, corre reglas, dispara notificaciones. Valida `INGEST_TOKEN`. Validar body con Zod. |
| `GET /api/tables` | Estado actual de cada mesa para el mapa. |
| `GET /api/alerts` | Alertas (filtro por estado). |
| `POST /api/alerts/:id/ack` | Marcar desde el dashboard (además de Telegram). |
| `GET /api/metrics` | Métricas agregadas del día. |
| `POST /api/telegram/webhook` | Comandos `/start`, `/mesa` y botones. |
| `POST /api/dev/simulate` | **Solo dev**: genera observaciones falsas para un escenario (mesa que pasa de lleno a vacío en 2 min). Clave para trabajar sin la visión. |

## Simulador (prioridad alta, hora 1–3)

`web/scripts/simulate.ts` (o la ruta `/api/dev/simulate`) que manda observaciones con el formato real, avanzando estados en el tiempo. Así backend, bot y dashboard se desarrollan y prueban sin cámara, y en la demo hay plan B si la cámara falla.

## Dashboard

Una sola pantalla, pensada para proyectar:

1. **Mapa del salón**: tarjetas/rectángulos por mesa con color según estado
   (`libre` gris, `servida` azul, `comiendo` verde, `por_terminar` amarillo, `terminada` rojo) y los platos como círculos con su estado. Contador de minutos en la mesa.
2. **Feed de alertas** en vivo (lado derecho): tipo, mesa, hace cuánto, quién la tomó.
3. **KPIs arriba**: tiempo medio de comida, tiempo medio de respuesta del mozo, alertas abiertas, platos devueltos llenos.
4. (Extra) **Vista cámara**: el frame anotado con las cajas. La visión puede servir un MJPEG local (`http://localhost:8001/stream`) que el dashboard muestra con `<img>` cuando corre en la misma red.

Actualización: polling cada 2 s con SWR alcanza. SSE solo si sobra tiempo.

## Estructura sugerida

```
web/
  app/
    page.tsx                 # dashboard
    api/observations/route.ts
    api/tables/route.ts
    api/alerts/route.ts
    api/metrics/route.ts
    api/telegram/webhook/route.ts
  lib/
    db/schema.ts
    rules/{config,engine}.ts
    rules/engine.test.ts
    notify/{telegram,whatsapp}.ts
  scripts/simulate.ts
```
