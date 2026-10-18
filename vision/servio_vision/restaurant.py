"""Datos del restaurante: carta, pedidos por mesa y mozo de cada mesa.

En producción vendrían del sistema del restaurante (comandas). Para la demo se leen
de `fixtures/restaurante/` (datos simulados): `carta.json` (platos y respuestas
típicas a "¿está todo bien?") y `salon.json` (mozo y pedido de cada mesa).

Cada plato que aparece en cámara se asigna al siguiente plato del pedido de su mesa,
en el orden en que aparecen (así lo haría la comanda: se sirve en ese orden). Si el
pedido se termina, el plato queda sin nombre ("un plato").
"""

import json
import threading
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "restaurante"
OTHER_ANSWER = "✏️ Otra respuesta"


@dataclass(frozen=True)
class Dish:
    dish_id: str
    name: str
    answers: tuple[str, ...]  # respuestas típicas a "¿está todo bien?"


class Restaurant:
    def __init__(self, dishes: dict[str, Dish], waiters: dict[str, str], tables: dict):
        self.dishes = dishes
        self.waiters = waiters  # id → nombre
        self.tables = tables  # table_id → {"mozo": id, "pedido": [dish_id, ...]}
        self._served: dict[str, dict[str, str]] = {}  # mesa → track_id → dish_id
        self._lock = threading.Lock()

    @classmethod
    def load(cls, directory: Path = DEFAULT_DIR) -> "Restaurant":
        carta = json.loads((directory / "carta.json").read_text(encoding="utf-8"))
        salon = json.loads((directory / "salon.json").read_text(encoding="utf-8"))
        dishes = {
            dish_id: Dish(dish_id, d["nombre"], tuple(d["respuestas"]))
            for dish_id, d in carta["platos"].items()
        }
        waiters = {wid: w["nombre"] for wid, w in salon["mozos"].items()}
        return cls(dishes, waiters, salon["mesas"])

    def waiter(self, table_id: str) -> str | None:
        table = self.tables.get(table_id)
        return self.waiters.get(table["mozo"]) if table else None

    def dish(self, table_id: str, track_id: str) -> Dish | None:
        """Plato del pedido que corresponde a ese plato de la cámara."""
        with self._lock:
            served = self._served.setdefault(table_id, {})
            if track_id not in served:
                order = self.tables.get(table_id, {}).get("pedido", [])
                if len(served) >= len(order):
                    return None
                served[track_id] = order[len(served)]
            return self.dishes.get(served[track_id])

    def has_order(self, table_id: str) -> bool:
        return bool(self.tables.get(table_id, {}).get("pedido"))

    def is_assigned(self, table_id: str, track_id: str) -> bool:
        with self._lock:
            return track_id in self._served.get(table_id, {})

    def unassigned(self, table_id: str) -> list[Dish]:
        """Platos del pedido de la mesa que todavía no se vieron en cámara."""
        with self._lock:
            served = list(self._served.get(table_id, {}).values())
            pending = list(self.tables.get(table_id, {}).get("pedido", []))
            for dish_id in served:
                if dish_id in pending:
                    pending.remove(dish_id)
            return [self.dishes[d] for d in pending if d in self.dishes]

    def assign(self, table_id: str, track_id: str, dish: Dish) -> None:
        """Este plato de la cámara es este plato del pedido (lo identificó la visión)."""
        with self._lock:
            self._served.setdefault(table_id, {})[track_id] = dish.dish_id

    def see_plates(self, table_id: str, track_ids: list[str]) -> None:
        """Registra los platos de una lectura, en orden, para asignarles el pedido."""
        for track_id in sorted(track_ids, key=_plate_number):
            self.dish(table_id, track_id)


def _plate_number(track_id: str) -> tuple[int, str]:
    """`T1-p12` → 12 (para ordenar p2 antes que p10)."""
    tail = track_id.rsplit("-p", 1)[-1]
    return (int(tail), track_id) if tail.isdigit() else (10**9, track_id)
