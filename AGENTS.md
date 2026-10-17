# AGENTS.md — Servio

Guía para agentes de IA (Claude Code, Kiro, etc.) y para el equipo. Leé este archivo primero y después **solo** los archivos de contexto que tu tarea necesite.

## Qué es Servio (en una línea)

Una cámara en el techo del restaurante detecta platos y bebidas en cada mesa, clasifica su estado (`lleno`, `comiendo`, `por_terminar`, `vacio`) y le manda un aviso al mozo por Telegram/WhatsApp cuando tiene que ir a esa mesa. Además genera métricas para el negocio.

Contexto: MVP de hackathon, 24 horas, equipo de 3 personas.

## Estado del repo

Hoy el repo tiene **solo documentación**. Las carpetas `vision/` y `web/` todavía no existen: se crean en la hora 1–6 del [plan](docs/context/08-plan-24h.md). Hasta entonces, "trabajar dentro de tu módulo" significa crear tu carpeta siguiendo [09-convenciones.md](docs/context/09-convenciones.md). Variables de entorno: ver [.env.example](.env.example).

## Mapa de contexto

| Archivo | Leelo cuando… |
|---|---|
| [docs/context/01-producto.md](docs/context/01-producto.md) | Necesitás entender el problema, el usuario, el alcance del MVP y qué queda afuera. |
| [docs/context/02-arquitectura.md](docs/context/02-arquitectura.md) | Vas a tocar más de un módulo o decidir dónde va algo. Stack, componentes y flujo de datos. |
| [docs/context/03-contratos.md](docs/context/03-contratos.md) | Vas a producir o consumir datos entre módulos. **Es la fuente de verdad** de esquemas, eventos y endpoints. |
| [docs/context/04-vision.md](docs/context/04-vision.md) | Trabajás en la cámara, detección, clasificación de estado o zonas de mesas. |
| [docs/context/05-reglas-alertas.md](docs/context/05-reglas-alertas.md) | Trabajás en la lógica que decide cuándo avisar (umbrales, suavizado, cooldowns). |
| [docs/context/06-notificaciones.md](docs/context/06-notificaciones.md) | Trabajás en el bot de Telegram o WhatsApp. |
| [docs/context/07-backend-dashboard.md](docs/context/07-backend-dashboard.md) | Trabajás en la API, la base de datos o el dashboard web y sus métricas. |
| [docs/context/08-plan-24h.md](docs/context/08-plan-24h.md) | Querés saber qué hacer ahora, quién hace qué y con qué herramienta. **Guía paso a paso.** |
| [docs/context/09-convenciones.md](docs/context/09-convenciones.md) | Antes de commitear: estructura del repo, ramas, estilo, variables de entorno. |
| [docs/context/10-demo-pitch.md](docs/context/10-demo-pitch.md) | Preparás la demo o la presentación final. |

## Reglas para agentes

1. **Los contratos mandan.** Si cambiás un esquema o un endpoint, actualizá primero `docs/context/03-contratos.md` en el mismo PR y avisá en la descripción.
2. **Trabajá solo dentro de tu módulo** (`vision/`, `api/`, `web/`) salvo que la tarea diga otra cosa. Ver [09-convenciones.md](docs/context/09-convenciones.md).
3. **Primero que funcione, después que sea lindo.** Es una hackathon: preferí lo simple y demostrable. Si dudás entre dos opciones, elegí la que se pueda mostrar antes.
4. **Siempre con mock.** Cada módulo tiene que poder correr solo, con datos falsos, sin depender de que el otro esté listo.
5. **Nada de secretos en el repo.** Tokens y claves van en `.env` (ignorado) y se documentan en `.env.example`.
6. **No guardes imágenes de clientes** salvo el dataset de entrenamiento que el equipo arme con su propio material.
7. Escribí docs y comentarios en español; código (nombres de variables, funciones) en inglés.
