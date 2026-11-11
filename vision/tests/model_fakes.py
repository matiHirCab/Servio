"""Un modelo falso para los tests del `hybrid`: reemplaza a `llm.make_estimator`, que es
lo que `HybridClassifier()` usa cuando no le pasan un estimador."""

from servio_vision import llm


class FakeEstimator:
    """Un `PlateEstimator` que llama a `answer(plates, table_id, references=...)`."""

    def __init__(self, answer):
        self.answer = answer

    def estimate(self, request):
        return self.answer(
            request.plate_jpegs, request.table_id, references=request.references
        )


def patch_estimator(monkeypatch, answer) -> None:
    monkeypatch.setattr(llm, "make_estimator", lambda *a, **k: FakeEstimator(answer))
