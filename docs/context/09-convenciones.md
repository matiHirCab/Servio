# 09 — Convenciones

## Estructura del repo

```
/
  AGENTS.md            # índice para agentes (leer primero)
  README.md
  .env.example         # todas las variables, sin valores
  docs/context/        # contexto del proyecto
  fixtures/            # escenarios JSON compartidos (ver 03-contratos.md)
  vision/              # Python: cámara, clasificador, pipeline (P1 + P3)
  web/                 # Next.js: API, reglas, bot, dashboard (P1 + P2 + P3)
  .github/workflows/   # CI
```

## Dueños por carpeta

"Trabajá solo dentro de tu módulo" (regla 2 de `AGENTS.md`) significa: solo en las carpetas de esta tabla. Para tocar la de otro, avisale y que revise el PR.

| Carpeta | Dueño |
|---|---|
| `vision/servio_vision/classifiers/` (salvo `fake.py`), `vision/servio_vision/prompt.py`, `vision/tools/` (salvo `draw_zones.py`) | P1 |
| `vision/` (resto: `main.py`, `capture.py`, `zones.py`, `change.py`, `smoothing.py`, `client.py`, `classifiers/fake.py`, `tools/draw_zones.py`, `config/`) | P3 |
| `vision/servio_vision/classifier.py`, `schemas.py` | P1 (contrato con P3: cambios en PR revisado por P3) |
| `vision/servio_vision/contracts.py` | P2 (espejo de `web/lib/contracts.ts`; cambios en PR revisado por P3, que lo usa en `client.py`) |
| `web/lib/notify/telegram.ts` | P1 |
| `web/app/api/`, `web/lib/` (resto), `web/scripts/` | P2 |
| `web/app/page.tsx`, `web/app/(dashboard)/`, `web/components/` | P3 |
| `fixtures/` | Compartido: `observations/` y `expected/` P2, `api/` P3. Cambios junto con `03-contratos.md`. |
| `docs/` | Todos, por PR |

## Código

- Docs y comentarios en español; identificadores en inglés.
- **Python** (`vision/`): 3.11, `venv` + `requirements.txt`, formato con `ruff format`, lint `ruff check`. Configuración por `.env`; los flags de CLI le ganan a las variables (por ejemplo `--demo` le gana a `DEMO_MODE`). Las dependencias de YOLO (`ultralytics`, torch) van en `requirements-yolo.txt` aparte, para que el CI no las instale.
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
CLAUDE_PROVIDER=bedrock     # bedrock | anthropic
ANTHROPIC_API_KEY=
YOLO_WEIGHTS=vision/models/best.pt

# web/
DATABASE_URL=
INGEST_TOKEN=
TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_SECRET=
TELEGRAM_GROUP_CHAT_ID=
NEXT_PUBLIC_USE_FIXTURES=false
DEMO_MODE=true
```

La lista completa y comentada está en `.env.example`.

## Privacidad

- La visión no sube imágenes al backend, solo JSON.
- A Claude/Bedrock solo se mandan recortes de la mesa, no el salón entero.
- Datasets e imágenes nunca se suben al repo (`vision/data/` y `vision/models/` están en `.gitignore`):
  - Material propio del equipo (videos de nuestra mesa): carpeta compartida del equipo o S3.
  - Dataset público *AI Smart Restaurant Surveillance* de Roboflow (CC BY 4.0): se baja en local para evaluar y entrenar; se cita en `vision/README.md`.
  - Videos de YouTube: solo uso interno para probar, no se muestran en la demo ni se redistribuyen.
