# web/

Next.js (App Router, TypeScript): API, motor de reglas, bot de Telegram y dashboard. Contexto: [docs/context/07-backend-dashboard.md](../docs/context/07-backend-dashboard.md), [05-reglas-alertas.md](../docs/context/05-reglas-alertas.md), [06-notificaciones.md](../docs/context/06-notificaciones.md). Contratos: [03-contratos.md](../docs/context/03-contratos.md).

Estado: **todavía sin código** (se crea en la hora 1–6 del plan).

## Arranque esperado (con mocks, sin visión)

```bash
npm install
cp ../.env.example ../.env   # completar DATABASE_URL, INGEST_TOKEN, etc.
npm run dev
npx tsx scripts/simulate.ts  # observaciones falsas con el formato real
npm test                     # Vitest: reglas, tableStatus(), reconciliación de ids
```
