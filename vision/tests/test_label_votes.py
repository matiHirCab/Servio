"""Estado mostrado por mayoría de 5 lecturas y por_terminar largo → vacío."""

from servio_vision.label_votes import LabelVotes
from servio_vision.schemas import Item


def _item(state, visible=True, pid="p1"):
    fill = {"lleno": 0.9, "comiendo": 0.5, "por_terminar": 0.2, "vacio": 0.0}[state]
    return Item(
        id=pid, position="centro", fill_ratio=fill, confidence=0.8, visible=visible
    )


def _feed(votes, states):
    return [votes.update([_item(s)])["p1"] for s in states]


def test_needs_five_readings():
    shown = _feed(LabelVotes(), ["lleno"] * 5)
    assert shown == [None, None, None, None, "lleno"]


def test_majority_of_last_five():
    shown = _feed(LabelVotes(), ["lleno", "comiendo", "lleno", "comiendo", "lleno"])
    assert shown[-1] == "lleno"
    # una lectura suelta distinta no cambia lo que se muestra
    shown = _feed(LabelVotes(), ["lleno"] * 4 + ["comiendo"])
    assert shown[-1] == "lleno"


def test_tie_goes_to_most_recent():
    votes = LabelVotes(window=4)
    shown = _feed(votes, ["lleno", "lleno", "comiendo", "comiendo"])
    assert shown[-1] == "comiendo"


def test_hidden_readings_do_not_vote():
    votes = LabelVotes()
    _feed(votes, ["comiendo"] * 5)
    for _ in range(3):
        shown = votes.update([_item("lleno", visible=False)])["p1"]
    assert shown == "comiendo"


def test_long_por_terminar_becomes_vacio():
    votes = LabelVotes(empty_after=20)
    shown = _feed(votes, ["por_terminar"] * 24)
    # se muestra por_terminar desde la lectura 5; a la 20.ª mostrándolo, pasa a vacío
    assert shown[4] == "por_terminar"
    assert shown[22] == "por_terminar"
    assert shown[23] == "vacio"


def test_por_terminar_count_resets():
    votes = LabelVotes(empty_after=20)
    _feed(votes, ["por_terminar"] * 20)
    shown = _feed(votes, ["comiendo"] * 3 + ["por_terminar"] * 3)
    assert "vacio" not in shown
