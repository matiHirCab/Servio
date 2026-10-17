"""Estado que se muestra de cada plato: mayoría de las últimas lecturas (pedido de
Tomás para probar_video).

- Un plato muestra un estado recién cuando tiene `WINDOW` lecturas, y es el de la
  mayoría de las últimas `WINDOW` (en empate, el más reciente).
- Si lo que se muestra queda en `por_terminar` durante `EMPTY_AFTER` lecturas
  seguidas, se muestra `vacio`.

Las lecturas con `visible=False` (tapado o «anterior») no votan. Es solo para lo que
se muestra: el suavizado de los avisos (`smoothing.py`) no cambia.
"""

from collections import Counter, deque

from servio_vision.schemas import Item, PlateState

WINDOW = 5
EMPTY_AFTER = 20


class LabelVotes:
    def __init__(self, window: int = WINDOW, empty_after: int = EMPTY_AFTER):
        self.window = window
        self.empty_after = empty_after
        self._votes: dict[str, deque] = {}
        self._finishing: dict[str, int] = {}  # lecturas seguidas mostrando por_terminar

    def update(self, items: list[Item]) -> dict[str, PlateState | None]:
        """Estado a mostrar por id (`None`: todavía no hay `window` lecturas)."""
        shown = {}
        for it in items:
            votes = self._votes.setdefault(it.id, deque(maxlen=self.window))
            if it.visible and it.state is not None:
                votes.append(it.state)
            state = _majority(votes) if len(votes) == self.window else None
            if state == "por_terminar":
                self._finishing[it.id] = self._finishing.get(it.id, 0) + 1
                if self._finishing[it.id] >= self.empty_after:
                    state = "vacio"
            else:
                self._finishing[it.id] = 0
            shown[it.id] = state
        return shown


def _majority(votes: deque) -> PlateState:
    counts = Counter(votes)
    top = max(counts.values())
    # en empate gana el más reciente
    return next(v for v in reversed(votes) if counts[v] == top)
