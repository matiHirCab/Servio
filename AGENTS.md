# AGENTS.md — Servio

Guía para agentes de IA (Claude Code, Kiro, etc.) y para el equipo. Leé este archivo primero y después **solo** los archivos de contexto que tu tarea necesite.

## Qué es Servio (en una línea)

Una cámara en alto mira el salón; YOLO encuentra los platos de cada mesa, Claude estima cuánta comida queda en cada uno, y cuando una mesa terminó, tiene platos vacíos o un plato sigue lleno mucho rato, le llega un aviso al mozo por Telegram.

Contexto: MVP de hackathon, 24 horas, equipo de 3 personas (Tomás, Matías, Jessica). Todo es un programa en Python dentro de `vision/`: no hay backend web ni dashboard. Lo que pasa (platos servidos, avisos, respuestas de los mozos) queda en una base SQLite (`vision/out/servio.db`) de la que salen las métricas (`tools/metricas.py`).

## Mapa de contexto

| Archivo | Leelo cuando… |
|---|---|
| [docs/context/01-producto.md](docs/context/01-producto.md) | Necesitás entender el problema, los avisos y qué queda afuera. |
| [docs/context/02-arquitectura.md](docs/context/02-arquitectura.md) | Vas a tocar más de una parte o decidir dónde va algo. |
| [docs/context/03-contratos.md](docs/context/03-contratos.md) | Vas a producir o consumir datos entre partes. **Es la fuente de verdad** de `Classifier`, `Item`, la observación de mesa, `Alert` y los fixtures. |
| [docs/context/04-vision.md](docs/context/04-vision.md) | Trabajás en captura, zonas, YOLO, Claude, suavizado o cadencias. |
| [docs/context/05-reglas-alertas.md](docs/context/05-reglas-alertas.md) | Trabajás en el estado de mesa, las reglas, recordatorios o cierre de avisos. |
| [docs/context/06-notificaciones.md](docs/context/06-notificaciones.md) | Trabajás en el envío a Telegram. |
| [docs/context/08-plan-24h.md](docs/context/08-plan-24h.md) | Querés el panorama: roles y fases por hora. |
| [docs/context/09-convenciones.md](docs/context/09-convenciones.md) | Antes de commitear: estructura, dueños por carpeta, ramas, estilo, variables de entorno. |
| [docs/context/11-tareas.md](docs/context/11-tareas.md) | Vas a hacer una tarea: qué hacer, en qué archivo, de qué depende y cuándo está hecha. |
| [docs/context/12-roles.md](docs/context/12-roles.md) | Vas a mostrar algo según quién mira (mozo, encargado, dueño): vistas, métricas y avisos por rol, y dónde se configura. |
| [docs/roadmap-producto.md](docs/roadmap-producto.md) | Vas a hacer una tarea de después de la hackathon (fases A–E): arquitectura, banco de pruebas en casa, mejoras y funciones nuevas. |

## Reglas para agentes

1. **Los contratos mandan.** Si cambiás `Classifier`, `Item`, la observación de mesa o `Alert`, actualizá primero `docs/context/03-contratos.md` y los fixtures en el mismo PR, y avisá en la descripción.
2. **Trabajá solo en tus carpetas** según la tabla «Dueños por carpeta» de [09-convenciones.md](docs/context/09-convenciones.md), salvo que la tarea diga otra cosa.
3. **Primero que funcione, después que sea lindo.** Es una hackathon: preferí lo simple y demostrable.
4. **Siempre con mock.** Cada parte tiene que poder correr sola: el pipeline con `--classifier fake`, las reglas con `fixtures/` y `--no-telegram`, el clasificador con fotos sueltas.
5. **Nada de secretos en el repo.** Tokens y claves van en `.env` (ignorado) y se documentan en `.env.example`.
6. **No subas imágenes, videos, datasets ni pesos al repo.** Ver «Privacidad y datos» en [09-convenciones.md](docs/context/09-convenciones.md).
7. Escribí docs y comentarios en español; código (nombres de variables, funciones) en inglés.
