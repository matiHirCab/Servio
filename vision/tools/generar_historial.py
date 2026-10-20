"""Genera un historial simulado de servicio (lo que acumularía Servio con el tiempo).

Datos simulados, a partir de la carta y el salón de `fixtures/restaurante/`: N días
con almuerzo y cena; en cada mesa se sirven platos y, según el plato, a veces vuelve
lleno (aviso + respuesta del mozo con una de las respuestas típicas); cada mesa que
termina tarda un tiempo en ser atendida, según el mozo. Sirve para mostrar las
métricas (`tools/metricas.py`) sin semanas de uso real.

    python tools/generar_historial.py [--dias 30] [--seed 7]

Escribe `fixtures/restaurante/historial.jsonl`, una fila por hecho:
- `{"tipo": "servido", "fecha", "servicio", "mesa", "mozo", "plato"}`
- `{"tipo": "plato_lleno", ..., "plato", "respuesta", "segundos_respuesta"}`
- `{"tipo": "mesa_terminada", ..., "minutos_hasta_atender"}`
"""

import argparse
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servio_vision.restaurant import DEFAULT_DIR, Restaurant

OUT = DEFAULT_DIR / "historial.jsonl"

# Qué tan seguido vuelve lleno cada plato, y qué responde el mozo (pesos por respuesta,
# en el orden de `respuestas` de carta.json). Los patrones son a propósito: un plato
# con problemas (risotto, casi siempre frío) y el resto bastante bien.
RETURNS_FULL = {
    "risotto": (0.18, [1, 6, 2, 1]),
    "lomo": (0.09, [2, 4, 2, 1]),
    "pasta": (0.07, [2, 3, 2, 1]),
    "ensalada": (0.06, [2, 3, 1, 1]),
    "milanesa": (0.04, [3, 1, 2, 1]),
    "donas": (0.05, [3, 1, 2, 1]),
    "cookies": (0.03, [4, 1, 1, 1]),
    "flan": (0.02, [4, 1, 1, 1]),
}
# Minutos hasta atender una mesa que terminó (media, desvío) y segundos en responder.
WAITERS = {
    "juan": ((3.0, 1.0), (40, 15)),
    "sofia": ((2.5, 0.8), (30, 10)),
    "martin": ((7.0, 2.5), (110, 40)),
    "lucia": ((4.0, 1.2), (55, 20)),
}
MEALS_PER_SERVICE = (3, 6)  # comidas por mesa en un servicio


def generate(restaurant: Restaurant, days: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    rows: list[dict] = []
    menu = list(restaurant.dishes)
    start = date.today() - timedelta(days=days)
    for day in range(days):
        fecha = (start + timedelta(days=day)).isoformat()
        for servicio in ("almuerzo", "cena"):
            for mesa, info in restaurant.tables.items():
                waiter_id = info["mozo"]
                waiter = restaurant.waiters[waiter_id]
                (attend_mu, attend_sd), (answer_mu, answer_sd) = WAITERS[waiter_id]
                base = {
                    "fecha": fecha,
                    "servicio": servicio,
                    "mesa": mesa,
                    "mozo": waiter,
                }
                for _ in range(rng.randint(*MEALS_PER_SERVICE)):
                    for dish_id in rng.sample(menu, rng.randint(1, 3)):
                        dish = restaurant.dishes[dish_id]
                        rows.append({"tipo": "servido", **base, "plato": dish.name})
                        rate, weights = RETURNS_FULL[dish_id]
                        if rng.random() < rate:
                            answer = rng.choices(dish.answers, weights=weights)[0]
                            seconds = max(5, round(rng.gauss(answer_mu, answer_sd)))
                            rows.append(
                                {
                                    "tipo": "plato_lleno",
                                    **base,
                                    "plato": dish.name,
                                    "respuesta": answer,
                                    "segundos_respuesta": seconds,
                                }
                            )
                    minutes = max(0.5, rng.gauss(attend_mu, attend_sd))
                    rows.append(
                        {
                            "tipo": "mesa_terminada",
                            **base,
                            "minutos_hasta_atender": round(minutes, 1),
                        }
                    )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Historial simulado de servicio.")
    parser.add_argument("--dias", type=int, default=30)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    rows = generate(Restaurant.load(), args.dias, args.seed)
    args.out.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )
    print(f"{len(rows)} filas en {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
