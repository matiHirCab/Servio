"""Contador de uso del modelo: cuántas llamadas, cuántos tokens y el tope por día."""

from datetime import date

from servio_vision.classifiers.usage import UsageMeter


class Today:
    def __init__(self) -> None:
        self.day = date(2026, 10, 29)

    def __call__(self) -> date:
        return self.day


def test_counts_calls_and_tokens():
    meter = UsageMeter(today=Today())
    assert meter.try_start(None)
    meter.add_usage({"input_tokens": 100, "output_tokens": 20, "cost_usd": 0.01})
    assert meter.try_start(None)
    meter.add_usage({"input_tokens": 50})  # openai solo informa la entrada
    meter.add_usage(None)
    assert meter.calls == 2
    assert meter.input_tokens == 150 and meter.output_tokens == 20
    assert meter.cost_usd == 0.01


def test_cap_stops_new_calls_for_the_day():
    meter = UsageMeter(today=Today())
    assert meter.try_start(2) and meter.try_start(2)
    assert not meter.try_start(2)
    assert meter.calls == 2  # la rechazada no cuenta


def test_no_cap_means_no_limit():
    meter = UsageMeter(today=Today())
    assert all(meter.try_start(None) for _ in range(1000))


def test_a_new_day_starts_from_zero():
    today = Today()
    meter = UsageMeter(today=today)
    meter.try_start(1)
    meter.add_usage({"input_tokens": 10})
    assert not meter.try_start(1)
    today.day = date(2026, 10, 30)
    assert meter.try_start(1)
    assert meter.calls == 1 and meter.input_tokens == 0


def test_cap_is_reported_once_per_day():
    today = Today()
    meter = UsageMeter(today=today)
    meter.try_start(1)
    assert not meter.try_start(1)
    assert meter.first_cap_report()  # la primera vez del día
    assert not meter.try_start(1)
    assert not meter.first_cap_report()
    today.day = date(2026, 10, 30)
    meter.try_start(1)
    assert not meter.try_start(1)
    assert meter.first_cap_report()  # otro día, otro aviso


def test_summary_is_due_every_n_calls():
    meter = UsageMeter(today=Today(), report_every=3)
    due = []
    for _ in range(7):
        meter.try_start(None)
        due.append(meter.report_due())
    assert due == [False, False, True, False, False, True, False]


def test_summary_text_has_the_numbers():
    meter = UsageMeter(today=Today())
    meter.try_start(None)
    meter.add_usage({"input_tokens": 1200, "output_tokens": 300, "cost_usd": 0.0123})
    text = meter.summary()
    assert "1 llamadas" in text and "1200" in text and "300" in text
    assert "0.0123" in text
