"""Tests del suavizado: un caso por regla de 04-vision.md."""

from servio_vision.schemas import Item
from servio_vision.smoothing import TableSmoother

FILL = {"lleno": 0.9, "comiendo": 0.5, "por_terminar": 0.2, "vacio": 0.0}


def plate(state: str, *, id: str = "p1", confidence: float = 0.9) -> Item:
    return Item(
        id=id,
        state=state,
        fill_ratio=FILL[state],
        position="centro",
        confidence=confidence,
    )


def hidden(id: str = "p1") -> Item:
    return Item(id=id, position="centro", visible=False)


def states(smoother: TableSmoother, *readings: list[Item]) -> list[dict[str, str]]:
    """Corre varias lecturas y devuelve {id: estado} después de cada una."""
    return [{i.id: i.state for i in smoother.update(r)} for r in readings]


def test_new_plate_enters_on_first_reading():
    assert states(TableSmoother(), [plate("lleno")]) == [{"p1": "lleno"}]


def test_neighbor_change_needs_two_readings():
    result = states(
        TableSmoother(),
        [plate("lleno")],
        [plate("comiendo")],
        [plate("comiendo")],
    )
    assert [r["p1"] for r in result] == ["lleno", "lleno", "comiendo"]


def test_interrupted_streak_starts_over():
    result = states(
        TableSmoother(),
        [plate("lleno")],
        [plate("comiendo")],
        [plate("lleno")],
        [plate("comiendo")],
        [plate("comiendo")],
    )
    assert [r["p1"] for r in result] == ["lleno"] * 4 + ["comiendo"]


def test_jump_needs_three_confident_readings():
    result = states(
        TableSmoother(),
        [plate("lleno")],
        [plate("vacio")],
        [plate("vacio")],
        [plate("vacio")],
    )
    assert [r["p1"] for r in result] == ["lleno", "lleno", "lleno", "vacio"]


def test_backward_change_needs_three_readings():
    result = states(
        TableSmoother(),
        [plate("comiendo")],
        [plate("lleno")],
        [plate("lleno")],
        [plate("lleno")],
    )
    assert [r["p1"] for r in result] == ["comiendo"] * 3 + ["lleno"]


def test_low_confidence_resets_hard_change():
    result = states(
        TableSmoother(),
        [plate("lleno")],
        [plate("vacio")],
        [plate("vacio")],
        [plate("vacio", confidence=0.5)],  # corta la racha: hacen falta 3 más
        [plate("vacio")],
        [plate("vacio")],
        [plate("vacio")],
    )
    assert [r["p1"] for r in result] == ["lleno"] * 6 + ["vacio"]


def test_hidden_plate_does_not_count():
    result = states(
        TableSmoother(),
        [plate("lleno")],
        [plate("comiendo")],
        [hidden()],  # no corta la racha ni cuenta como ausencia
        [hidden()],
        [plate("comiendo")],
    )
    assert [r["p1"] for r in result] == ["lleno"] * 4 + ["comiendo"]


def test_hidden_new_plate_is_ignored():
    assert states(TableSmoother(), [hidden("p9")]) == [{}]


def test_absent_once_is_kept_twice_is_removed():
    result = states(
        TableSmoother(),
        [plate("lleno"), plate("lleno", id="p2")],
        [plate("lleno")],
        [plate("lleno")],
    )
    assert result == [
        {"p1": "lleno", "p2": "lleno"},
        {"p1": "lleno", "p2": "lleno"},
        {"p1": "lleno"},
    ]


def test_absence_counter_resets_when_plate_comes_back():
    result = states(
        TableSmoother(),
        [plate("lleno")],
        [],
        [plate("lleno")],
        [],
    )
    assert result == [{"p1": "lleno"}] * 4


def test_output_fill_ratio_matches_smoothed_state():
    smoother = TableSmoother()
    smoother.update([plate("lleno")])
    [item] = smoother.update([plate("comiendo")])  # todavía no se acepta
    assert item.state == "lleno"
    assert item.fill_ratio == FILL["lleno"]


def test_pending_change_keeps_latest_box_but_not_none():
    smoother = TableSmoother()
    first = plate("lleno").model_copy(update={"bbox": (0.1, 0.1, 0.2, 0.2)})
    smoother.update([first])
    moved = plate("comiendo").model_copy(update={"bbox": (0.5, 0.5, 0.2, 0.2)})
    [item] = smoother.update([moved])  # cambio pendiente: estado viejo, caja nueva
    assert item.state == "lleno" and item.bbox == (0.5, 0.5, 0.2, 0.2)
    [item] = smoother.update([plate("lleno")])  # sin caja: se conserva la anterior
    assert item.bbox == (0.5, 0.5, 0.2, 0.2)
