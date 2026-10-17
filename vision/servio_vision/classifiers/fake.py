"""Clasificador falso, sin modelo (JES-2).

Sirve para armar y probar el pipeline (loop, suavizado, alertas, vista de
demo) sin YOLO ni Claude, y como plan B de la demo.

`classify()` no recibe la hora, así que los dos modos avanzan por **lecturas**
(una llamada por mesa = una lectura): con `--interval 30`, 4 lecturas son 2 min.

- `mode="script"`: cada mesa simula una comida en loop. Los platos aparecen
  llenos, se van comiendo, quedan vacíos y se retiran; después de una pausa
  sin platos, vuelve a empezar. Cada mesa arranca desfasada para que no
  cambien todas a la vez.
- `mode="replay"`: devuelve, en orden, los platos de cada mesa leídos de un
  `.jsonl` de `fixtures/observations/` (formato de 03-contratos.md). Cuando
  se termina el archivo, devuelve `None` (lectura fallida).

    get_classifier("fake")                                   # script
    get_classifier("fake", mode="replay", fixture="fixtures/observations/mesa_completa.jsonl")
"""

import json
import logging
from collections import defaultdict
from pathlib import Path

from servio_vision.schemas import Item, PlateState, TableReport

logger = logging.getLogger(__name__)

# (estado, lecturas que dura); None = sin platos en la mesa
DEFAULT_SCRIPT: list[tuple[PlateState | None, int]] = [
    ("lleno", 4),
    ("comiendo", 4),
    ("por_terminar", 3),
    ("vacio", 3),
    (None, 3),
]
FILL_RATIO: dict[PlateState, float] = {
    "lleno": 0.9,
    "comiendo": 0.55,
    "por_terminar": 0.15,
    "vacio": 0.02,
}
POSITIONS = ["izquierda", "derecha", "arriba", "abajo"]
# caja [x, y, w, h] normalizada dentro del recorte de la mesa, por posición
BBOXES = {
    "izquierda": (0.08, 0.3, 0.38, 0.4),
    "derecha": (0.54, 0.3, 0.38, 0.4),
    "arriba": (0.31, 0.05, 0.38, 0.3),
    "abajo": (0.31, 0.65, 0.38, 0.3),
}
TABLE_OFFSET = 3  # lecturas de desfase entre una mesa y la siguiente


class FakeClassifier:
    def __init__(
        self,
        mode: str = "script",
        fixture: str | Path | None = None,
        plates: int = 2,
        script: list[tuple[PlateState | None, int]] | None = None,
    ) -> None:
        if mode not in ("script", "replay"):
            raise ValueError(f"Modo desconocido: {mode!r} (script | replay)")
        if mode == "replay" and fixture is None:
            raise ValueError('El modo "replay" necesita fixture=<archivo .jsonl>')
        self.mode = mode
        self.plates = plates
        self.script = script or DEFAULT_SCRIPT
        self._readings: dict[str, int] = defaultdict(int)  # lecturas por mesa
        self._replay = _load_fixture(Path(fixture)) if fixture is not None else {}

    def classify(
        self, image: bytes, table_id: str, previous_items: list[Item]
    ) -> TableReport | None:
        reading = self._readings[table_id]
        self._readings[table_id] += 1
        if self.mode == "replay":
            return self._replay_reading(table_id, reading)
        return self._script_reading(table_id, reading)

    def _script_reading(self, table_id: str, reading: int) -> TableReport:
        cycle = sum(n for _, n in self.script)
        step = (reading + _table_index(table_id) * TABLE_OFFSET) % cycle
        meal = (reading + _table_index(table_id) * TABLE_OFFSET) // cycle
        state = None
        for state, length in self.script:
            if step < length:
                break
            step -= length
        if state is None:
            return TableReport(items=[], people_present=True)
        # ids nuevos en cada comida, como platos nuevos servidos
        items = [
            Item(
                id=f"p{meal * self.plates + n + 1}",
                state=state,
                fill_ratio=FILL_RATIO[state],
                position=POSITIONS[n % len(POSITIONS)],
                bbox=BBOXES[POSITIONS[n % len(POSITIONS)]],
                confidence=0.9,
            )
            for n in range(self.plates)
        ]
        return TableReport(items=items, people_present=True)

    def _replay_reading(self, table_id: str, reading: int) -> TableReport | None:
        readings = self._replay.get(table_id, [])
        if reading >= len(readings):
            if reading == len(readings):
                logger.warning("Replay: no hay más lecturas para la mesa %s", table_id)
            return None
        return readings[reading]


def _table_index(table_id: str) -> int:
    """T1 → 0, T2 → 1…; si no termina en número, 0."""
    digits = "".join(c for c in table_id if c.isdigit())
    return int(digits) - 1 if digits else 0


def _load_fixture(path: Path) -> dict[str, list[TableReport]]:
    """Lee un .jsonl de observaciones y lo agrupa por mesa, en orden."""
    by_table: dict[str, list[TableReport]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obs = json.loads(line)
        prefix = f"{obs['table_id']}-"
        items = [
            Item(
                id=plate["track_id"].removeprefix(prefix),
                state=plate["state"],
                fill_ratio=plate.get("fill_ratio"),
                position=plate["position"],
                confidence=plate.get("confidence", 1.0),
            )
            for plate in obs["items"]
        ]
        people = obs.get("people_present")
        by_table[obs["table_id"]].append(
            TableReport(items=items, people_present=True if people is None else people)
        )
    return dict(by_table)
