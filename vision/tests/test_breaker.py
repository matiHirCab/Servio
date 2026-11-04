"""Cortacircuito del modelo: con la API caída no se gasta un intento por mesa y lectura."""

from servio_vision.classifiers.breaker import CircuitBreaker


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make(clock: Clock) -> CircuitBreaker:
    return CircuitBreaker(
        failures_to_open=3, cooldown_s=60, max_cooldown_s=600, clock=clock
    )


def test_closed_until_enough_consecutive_failures():
    breaker = make(Clock())
    for _ in range(2):
        assert breaker.allow()
        breaker.record_failure()
    assert breaker.allow()  # van dos: todavía cerrado
    breaker.record_failure()
    assert not breaker.allow()  # la tercera lo abre


def test_a_success_resets_the_count():
    breaker = make(Clock())
    for _ in range(2):
        breaker.record_failure()
    breaker.record_success()
    for _ in range(2):
        breaker.record_failure()
    assert breaker.allow()


def test_after_the_cooldown_one_probe_goes_through():
    clock = Clock()
    breaker = make(clock)
    for _ in range(3):
        breaker.record_failure()
    clock.now = 59
    assert not breaker.allow()
    clock.now = 61
    assert breaker.allow()  # la prueba
    assert not breaker.allow()  # las demás esperan a su resultado


def test_failures_of_calls_already_in_flight_do_not_extend_the_pause():
    """Con varias mesas en paralelo, cuando el cortacircuito se abre quedan llamadas
    que ya habían pasado. Que fallen después no es una prueba fallida."""
    clock = Clock()
    breaker = make(clock)
    for _ in range(3):
        breaker.record_failure()  # se abre: 60 s
    for _ in range(5):
        breaker.record_failure()  # las que venían en vuelo
    clock.now = 61
    assert breaker.allow()  # la pausa sigue siendo de 60 s, desde que se abrió
    breaker.record_failure()  # ahora sí, la prueba falló: el doble
    clock.now = 61 + 119
    assert not breaker.allow()


def test_a_permanent_failure_in_flight_lengthens_the_pause():
    clock = Clock()
    breaker = make(clock)
    for _ in range(3):
        breaker.record_failure()
    breaker.record_failure(permanent=True)  # la clave inválida se supo recién ahora
    clock.now = 599
    assert not breaker.allow()
    clock.now = 601
    assert breaker.allow()


def test_a_cancelled_probe_lets_the_next_caller_probe():
    """Si la llamada de prueba no llega a hacerse (tope diario), no queda trabado."""
    clock = Clock()
    breaker = make(clock)
    for _ in range(3):
        breaker.record_failure()
    clock.now = 61
    assert breaker.allow()
    breaker.cancel_probe()
    assert breaker.allow()


def test_a_successful_probe_closes_it():
    clock = Clock()
    breaker = make(clock)
    for _ in range(3):
        breaker.record_failure()
    clock.now = 61
    assert breaker.allow()
    breaker.record_success()
    assert breaker.allow() and breaker.allow()


def test_a_failed_probe_reopens_with_a_longer_cooldown():
    clock = Clock()
    breaker = make(clock)
    for _ in range(3):
        breaker.record_failure()
    clock.now = 61
    assert breaker.allow()
    breaker.record_failure()  # la prueba falló: ahora espera el doble
    clock.now = 61 + 119
    assert not breaker.allow()
    clock.now = 61 + 121
    assert breaker.allow()


def test_cooldown_stops_doubling_at_the_cap():
    clock = Clock()
    breaker = make(clock)
    for _ in range(3):
        breaker.record_failure()
    for _ in range(10):
        clock.now += 1000
        assert breaker.allow()
        breaker.record_failure()
    clock.now += 599
    assert not breaker.allow()
    clock.now += 2
    assert breaker.allow()  # nunca más de 600 s


def test_a_permanent_failure_opens_it_at_once_with_the_longest_cooldown():
    clock = Clock()
    breaker = make(clock)
    breaker.record_failure(permanent=True)  # una clave inválida no se arregla sola
    assert not breaker.allow()
    clock.now = 599
    assert not breaker.allow()
    clock.now = 601
    assert breaker.allow()


def test_reports_when_it_opens_and_closes():
    clock = Clock()
    events: list[str] = []
    breaker = CircuitBreaker(
        failures_to_open=1,
        cooldown_s=60,
        max_cooldown_s=600,
        clock=clock,
        on_change=events.append,
    )
    breaker.record_failure()
    breaker.record_failure()  # ya abierto: no avisa otra vez
    clock.now = 61
    breaker.allow()
    breaker.record_success()
    assert events == ["open", "closed"]
