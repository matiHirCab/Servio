import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from eval import summarize


def test_summarize():
    results = [
        {"estado": "lleno", "predicho": "lleno", "fill_ratio": 0.9},
        {"estado": "lleno", "predicho": "comiendo", "fill_ratio": 0.7},
        {"estado": "vacio", "predicho": "lleno", "fill_ratio": 0.9},
        {"estado": "vacio", "predicho": None, "fill_ratio": None},
    ]
    s = summarize(results)
    assert (s["platos"], s["sin_respuesta"]) == (4, 1)
    assert s["acierto"] == round(1 / 3, 3)
    assert s["a_un_estado"] == round(2 / 3, 3)
    assert s["matriz"]["lleno"] == {"lleno": 1, "comiendo": 1}
    assert s["fill_ratio_medio"]["lleno"] == 0.8
