# 09 — Convenciones

## Estructura del repo

```
/
  AGENTS.md            # índice para agentes (leer primero)
  README.md
  .env.example         # todas las variables, sin valores
  docs/context/        # contexto del proyecto
  vision/              # Python: cámara + modelo (P1)
  web/                 # Next.js: API, reglas, bot, dashboard (P2 + P3)
  .github/workflows/   # CI
```

## Código

- Docs y comentarios en español; identificadores en inglés.
- **Python** (`vision/`): 3.11, `venv` + `requirements.txt`, formato con `ruff format`, lint `ruff check`. Configuración por argumentos CLI o `.env`.
- **TypeScript** (`web/`): `strict`, Zod para validar todo lo que entra por API, ESLint + Prettier por defecto de Next.
- Los valores de enums son exactamente los de [03-contratos.md](03-contratos.md) (`lleno`, `comiendo`, `por_terminar`, `vacio`…). No traducir ni renombrar.

## Git

- Ramas: `p1/<tema>`, `p2/<tema>`, `p3/<tema>`.
- Commits cortos en presente: `vision: agrega suavizado por voto mayoritario`.
- PR chico a `main`, otra persona aprueba. `main` siempre deployable (Vercel deploya `main` a producción y cada PR a un preview).

## Variables de entorno (`.env.example`)

```
# vision/
SERVIO_API_URL=http://localhost:3000
INGEST_TOKEN=
AWS_REGION=us-east-1
AWS_PROFILE=
BEDROCK_MODEL_ID=

# web/
DATABASE_URL=
INGEST_TOKEN=
TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_SECRET=
DEMO_MODE=true
```

## Privacidad

- La visión no sube imágenes al backend, solo JSON.
- A Claude/Bedrock solo se mandan recortes de la mesa, no el salón entero.
- El dataset de entrenamiento es material propio del equipo; no subir fotos de terceros al repo (usar S3).
