"""Tests de Claude por plato con el proveedor mockeado (TOM-3)."""

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from servio_vision.classifiers import claude_plates
from servio_vision.classifiers.claude_plates import ClaudeCallError, estimate_plates

JPEG = b"\xff\xd8fake"
REQUEST = claude_plates.PlatesRequest(
    claude_plates.build_content(JPEG, [JPEG], "T1"), [False]
)


def _answer(n, people=True):
    plates = [
        {"index": i, "fill_ratio": 0.5, "confidence": 0.8, "visible": True}
        for i in range(1, n + 1)
    ]
    return {"plates": plates, "people_present": people}


@pytest.fixture
def backend(monkeypatch):
    """Reemplaza el proveedor `cli` por respuestas en cola."""
    calls = []
    answers = []

    def fake(request, model, timeout_s):
        calls.append(
            {"content": request.content, "model": model, "timeout_s": timeout_s}
        )
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer, {"input_tokens": 10, "output_tokens": 5}

    monkeypatch.setitem(claude_plates._BACKENDS, "cli", fake)
    monkeypatch.setattr(claude_plates, "RETRY_WAIT_S", 0)
    monkeypatch.delenv("CLAUDE_PROVIDER", raising=False)
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    monkeypatch.delenv("CLAUDE_TIMEOUT_S", raising=False)
    return calls, answers


def test_three_plates(backend):
    calls, answers = backend
    answers.append(_answer(3, people=False))
    report = estimate_plates(JPEG, [JPEG] * 3, "T1")
    assert [p.index for p in report.plates] == [1, 2, 3]
    assert report.people_present is False
    # mesa + 3 platos = 4 imágenes, en ese orden
    images = [b for b in calls[0]["content"] if b["type"] == "image"]
    assert len(images) == 4
    assert calls[0]["model"] == "haiku"
    assert calls[0]["timeout_s"] == 15.0


def test_reference_goes_before_its_plate(backend):
    calls, answers = backend
    answers.append(_answer(2))
    estimate_plates(b"mesa", [b"p1", b"p2"], "T1", references=[None, b"ref2"])
    images = [
        claude_plates.base64.b64decode(b["source"]["data"])
        for b in calls[0]["content"]
        if b["type"] == "image"
    ]
    assert images == [b"mesa", b"p1", b"ref2", b"p2"]


def test_retries_once_then_succeeds(backend):
    calls, answers = backend
    answers += [ClaudeCallError("timeout"), _answer(2)]
    assert estimate_plates(JPEG, [JPEG] * 2, "T1") is not None
    assert len(calls) == 2
    # el contenido se arma una sola vez, no en cada intento
    assert calls[0]["content"] is calls[1]["content"]


def test_references_of_another_length_is_a_bug_not_a_retry(backend):
    calls, _ = backend
    with pytest.raises(ValueError):
        estimate_plates(JPEG, [JPEG] * 2, "T1", references=[None])
    assert calls == []


def test_returns_none_after_two_failures(backend):
    calls, answers = backend
    answers += [ClaudeCallError("x"), {"plates": "mal"}]
    assert estimate_plates(JPEG, [JPEG], "T1") is None
    assert len(calls) == 2


def test_wrong_plate_count_is_retried(backend):
    _, answers = backend
    answers += [_answer(1), _answer(1)]
    assert estimate_plates(JPEG, [JPEG] * 2, "T1") is None


def test_out_of_range_fill_ratio_is_rejected(backend):
    _, answers = backend
    bad = _answer(1)
    bad["plates"][0]["fill_ratio"] = 1.4
    answers += [bad, bad]
    assert estimate_plates(JPEG, [JPEG], "T1") is None


def test_unordered_answer_is_sorted(backend):
    _, answers = backend
    answer = _answer(2)
    answer["plates"].reverse()
    answers.append(answer)
    report = estimate_plates(JPEG, [JPEG] * 2, "T1")
    assert [p.index for p in report.plates] == [1, 2]


def test_no_plates_skips_claude(backend):
    calls, _ = backend
    report = estimate_plates(JPEG, [], "T1")
    assert report.plates == []
    assert calls == []


def test_env_overrides(backend, monkeypatch):
    calls, answers = backend
    monkeypatch.setenv("CLAUDE_MODEL", "sonnet")
    monkeypatch.setenv("CLAUDE_TIMEOUT_S", "20")
    answers.append(_answer(1))
    estimate_plates(JPEG, [JPEG], "T1")
    assert calls[0]["model"] == "sonnet"
    assert calls[0]["timeout_s"] == 20.0


def test_unknown_provider():
    with pytest.raises(ValueError):
        estimate_plates(JPEG, [JPEG], "T1", provider="bedrock")


def test_cli_reads_structured_output(monkeypatch):
    """El proveedor `cli` toma `structured_output` del evento `result`."""
    result = {
        "type": "result",
        "is_error": False,
        "structured_output": _answer(1),
        "usage": {"input_tokens": 3, "output_tokens": 2},
        "total_cost_usd": 0.001,
    }
    stdout = json.dumps({"type": "system"}) + "\n" + json.dumps(result) + "\n"

    def fake_run(args, **kwargs):
        assert "--json-schema" in args
        content = json.loads(kwargs["input"])["message"]["content"]
        assert content[0]["text"] == claude_plates.prompt.SYSTEM_PROMPT
        assert content[1:] == REQUEST.content
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(claude_plates, "_find_cli", lambda: "claude")
    monkeypatch.setattr(claude_plates.subprocess, "run", fake_run)
    data, usage = claude_plates._call_cli(REQUEST, "haiku", 10)
    assert data == _answer(1)
    assert usage["cost_usd"] == 0.001


def test_cli_timeout_is_a_call_error(monkeypatch):
    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 10)

    monkeypatch.setattr(claude_plates, "_find_cli", lambda: "claude")
    monkeypatch.setattr(claude_plates.subprocess, "run", fake_run)
    with pytest.raises(ClaudeCallError):
        claude_plates._call_cli(REQUEST, "haiku", 10)


@pytest.mark.parametrize(
    "text",
    [
        '{"plates": [], "people_present": true}',
        '```json\n{"plates": [], "people_present": true}\n```',
        'Acá va:\n{"plates": [], "people_present": true}',
    ],
)
def test_json_from_text(text):
    assert claude_plates.json_from_text(text) == {"plates": [], "people_present": True}


def test_json_from_text_without_json_shows_the_answer():
    with pytest.raises(ClaudeCallError, match="no puedo ver"):
        claude_plates.json_from_text("no puedo ver las imágenes")


def test_cli_on_windows_cmd_skips_schema_and_parses_text(monkeypatch):
    """Con claude.cmd no se pasan argumentos con comillas y el JSON se lee del texto."""
    result = {
        "type": "result",
        "is_error": False,
        "result": "```json\n" + json.dumps(_answer(1)) + "\n```",
    }

    def fake_run(args, **kwargs):
        assert "--json-schema" not in args
        assert "--system-prompt" not in args
        content = json.loads(kwargs["input"])["message"]["content"]
        assert content[0]["type"] == "text"  # el prompt va en el mensaje
        return subprocess.CompletedProcess(
            args, 0, stdout=json.dumps(result), stderr=""
        )

    monkeypatch.setattr(claude_plates, "_find_cli", lambda: r"C:\npm\claude.cmd")
    monkeypatch.setattr(claude_plates.subprocess, "run", fake_run)
    data, _ = claude_plates._call_cli(REQUEST, "haiku", 10)
    assert data == _answer(1)


def test_cli_uses_the_answer_even_if_claude_exits_with_error(monkeypatch):
    """Si claude respondió el JSON y después salió con error, la respuesta sirve."""
    result = {"type": "result", "is_error": True, "result": json.dumps(_answer(1))}

    def fake_run(args, **kwargs):
        assert kwargs["env"]["DISABLE_AUTOUPDATER"] == "1"
        return subprocess.CompletedProcess(
            args, 1, stdout=json.dumps(result), stderr=""
        )

    monkeypatch.setattr(claude_plates, "_find_cli", lambda: "claude")
    monkeypatch.setattr(claude_plates.subprocess, "run", fake_run)
    data, _ = claude_plates._call_cli(REQUEST, "haiku", 10)
    assert data == _answer(1)


def test_cli_error_without_json_is_a_call_error(monkeypatch):
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="se cortó")

    monkeypatch.setattr(claude_plates, "_find_cli", lambda: "claude")
    monkeypatch.setattr(claude_plates.subprocess, "run", fake_run)
    with pytest.raises(ClaudeCallError, match="se cortó"):
        claude_plates._call_cli(REQUEST, "haiku", 10)


class _FakeOpenAI:
    """Módulo `openai` falso: guarda el pedido y devuelve respuestas en cola."""

    class OpenAIError(Exception):
        pass

    def __init__(self, responses):
        self.requests = []
        self.responses = responses
        fake = self

        class Client:
            def __init__(self, **kwargs):
                fake.client_kwargs = kwargs
                self.decisions = self

            def create(self, **kwargs):
                fake.requests.append(kwargs)
                answers = fake.responses.pop(0)
                if isinstance(answers, Exception):
                    raise answers
                return SimpleNamespace(
                    answers=answers, usage=SimpleNamespace(input_tokens=42)
                )

        self.OpenAI = Client


def _decision(type_, name, **fields):
    return SimpleNamespace(type=type_, name=name, **fields)


def _decisions(
    n, refs=(), fill_score=2.0, visible=0.9, same=0.9, people=0.8, probabilities=None
):
    answers = []
    for i in range(1, n + 1):
        fill = _decision("score", f"fill_{i}", score=fill_score, confidence=0.7)
        if probabilities is not None:
            fill.probabilities = probabilities
        answers.append(fill)
        answers.append(_decision("predicate", f"visible_{i}", probability=visible))
        if i in refs:
            answers.append(_decision("predicate", f"same_plate_{i}", probability=same))
    answers.append(_decision("predicate", "people_present", probability=people))
    return answers


@pytest.fixture
def openai_api(monkeypatch):
    fake = _FakeOpenAI([])
    monkeypatch.setitem(sys.modules, "openai", fake)
    monkeypatch.setattr(claude_plates, "RETRY_WAIT_S", 0)
    monkeypatch.setenv("CLAUDE_PROVIDER", "openai")
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("CLAUDE_TIMEOUT_S", raising=False)
    monkeypatch.delenv("OPENAI_TIMEOUT_S", raising=False)
    return fake


def test_openai_maps_answers_to_report(openai_api):
    openai_api.responses.append(_decisions(2, refs={2}, fill_score=1.0, same=0.2))
    report = estimate_plates(b"mesa", [b"p1", b"p2"], "T1", references=[None, b"ref2"])
    assert [p.fill_ratio for p in report.plates] == [0.15, 0.15]  # "restos"
    assert [p.confidence for p in report.plates] == [0.7, 0.7]
    assert [p.same_plate for p in report.plates] == [True, False]
    assert report.people_present is True
    request = openai_api.requests[0]
    assert request["model"] == "gpt-6-luna"
    assert openai_api.client_kwargs["timeout"] == 20.0
    names = [q["name"] for q in request["questions"]]
    assert names == [
        "fill_1",
        "visible_1",
        "fill_2",
        "visible_2",
        "same_plate_2",
        "people_present",
    ]


def test_openai_sends_images_as_data_urls(openai_api):
    openai_api.responses.append(_decisions(1))
    estimate_plates(b"mesa", [b"p1"], "T1")
    parts = openai_api.requests[0]["input"][0]["content"]
    assert parts[0]["text"] == claude_plates.prompt.INSTRUCTIONS
    # con preguntas tipadas no se pide JSON en ningún lado
    assert not any("JSON" in p.get("text", "") for p in parts)
    images = [p["image_url"] for p in parts if p["type"] == "input_image"]
    assert images == [
        "data:image/jpeg;base64," + claude_plates.base64.b64encode(b).decode()
        for b in (b"mesa", b"p1")
    ]


def test_openai_low_visibility_marks_not_visible(openai_api):
    openai_api.responses.append(_decisions(1, visible=0.1, people=0.1))
    report = estimate_plates(JPEG, [JPEG], "T1")
    assert report.plates[0].visible is False
    assert report.people_present is False


def test_openai_refusal_retries_then_none(openai_api):
    refusal = [_decision("refusal", "fill_1")]
    openai_api.responses += [refusal, refusal]
    assert estimate_plates(JPEG, [JPEG], "T1") is None
    assert len(openai_api.requests) == 2


def test_openai_api_error_then_success(openai_api):
    openai_api.responses += [_FakeOpenAI.OpenAIError("429"), _decisions(1)]
    assert estimate_plates(JPEG, [JPEG], "T1") is not None


def test_openai_missing_answer_is_retried(openai_api):
    incomplete = _decisions(2)[:-2]  # falta visible_2 y people_present
    openai_api.responses += [incomplete, incomplete]
    assert estimate_plates(JPEG, [JPEG] * 2, "T1") is None


def test_openai_score_none_is_retried(openai_api):
    answers = _decisions(1)
    answers[0].score = None
    openai_api.responses += [answers, answers]
    assert estimate_plates(JPEG, [JPEG], "T1") is None


def test_openai_score_out_of_scale_is_rejected(openai_api):
    # 5 niveles: score va de 0 a 4; un 5 es otra escala y no se recorta a 1
    too_high = _decisions(1, fill_score=5.0)
    openai_api.responses += [too_high, too_high]
    assert estimate_plates(JPEG, [JPEG], "T1") is None


def test_openai_top_score_is_full(openai_api):
    openai_api.responses.append(_decisions(1, fill_score=4.0))
    assert estimate_plates(JPEG, [JPEG], "T1").plates[0].fill_ratio == 1.0


def test_openai_refusal_without_name_is_retried(openai_api):
    refusal = [SimpleNamespace(type="refusal")]
    openai_api.responses += [refusal, refusal]
    assert estimate_plates(JPEG, [JPEG], "T1") is None


def test_openai_duplicate_answer_is_rejected(openai_api):
    dup = _decisions(1) + [_decision("score", "fill_1", score=4.0, confidence=0.9)]
    openai_api.responses += [dup, dup]
    assert estimate_plates(JPEG, [JPEG], "T1") is None


def test_openai_unexpected_answer_is_rejected(openai_api):
    extra = _decisions(1) + [_decision("predicate", "same_plate_1", probability=0.9)]
    openai_api.responses += [extra, extra]
    assert estimate_plates(JPEG, [JPEG], "T1") is None


def test_openai_ignores_claude_model(openai_api, monkeypatch):
    monkeypatch.setenv("CLAUDE_MODEL", "haiku")
    openai_api.responses.append(_decisions(1))
    estimate_plates(JPEG, [JPEG], "T1")
    assert openai_api.requests[0]["model"] == "gpt-6-luna"


def test_openai_model_env(openai_api, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-otro")
    openai_api.responses.append(_decisions(1))
    estimate_plates(JPEG, [JPEG], "T1")
    assert openai_api.requests[0]["model"] == "gpt-otro"


def test_openai_missing_package_falls_back(openai_api, monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)  # import openai → ImportError
    assert estimate_plates(JPEG, [JPEG], "T1") is None


def test_fill_levels_land_in_their_state():
    from servio_vision.schemas import state_from_fill_ratio

    prompt = claude_plates.prompt
    assert len(prompt.FILL_RATIOS) == len(prompt.FILL_LEVELS)
    states = [state_from_fill_ratio(r) for r in prompt.FILL_RATIOS]
    assert states == ["vacio", "por_terminar", "comiendo", "comiendo", "lleno"]


def test_openai_fractional_score_is_interpolated(openai_api):
    openai_api.responses.append(_decisions(1, fill_score=2.5))
    report = estimate_plates(JPEG, [JPEG], "T1")
    assert report.plates[0].fill_ratio == pytest.approx(0.55)  # entre mitad y parte


def _level_probs(**by_label):
    return [{"label": label, "probability": p} for label, p in by_label.items()]


def test_openai_fill_ratio_is_the_expected_value(openai_api):
    # score 2.0 interpolado daría 0.45; con las probabilidades da el valor esperado
    probs = _level_probs(vacío=0.5, intacto=0.5)
    openai_api.responses.append(_decisions(1, fill_score=2.0, probabilities=probs))
    report = estimate_plates(JPEG, [JPEG], "T1")
    assert report.plates[0].fill_ratio == pytest.approx(0.5)


def test_openai_unknown_level_label_falls_back_to_score(openai_api):
    probs = _level_probs(otro=1.0)
    openai_api.responses.append(_decisions(1, fill_score=4.0, probabilities=probs))
    assert estimate_plates(JPEG, [JPEG], "T1").plates[0].fill_ratio == 1.0


def test_openai_old_sdk_without_decisions_falls_back(openai_api, monkeypatch):
    class OldClient:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(openai_api, "OpenAI", OldClient)
    assert estimate_plates(JPEG, [JPEG], "T1") is None


@pytest.mark.parametrize("probability", [float("nan"), 50.0])
def test_openai_bad_probability_is_rejected(openai_api, probability):
    bad = _decisions(1, people=probability)
    openai_api.responses += [bad, bad]
    assert estimate_plates(JPEG, [JPEG], "T1") is None


def test_openai_has_its_own_timeout(openai_api, monkeypatch):
    monkeypatch.setenv("CLAUDE_TIMEOUT_S", "10")
    openai_api.responses.append(_decisions(1))
    estimate_plates(JPEG, [JPEG], "T1")
    assert openai_api.client_kwargs["timeout"] == 20.0
    monkeypatch.setenv("OPENAI_TIMEOUT_S", "30")
    openai_api.responses.append(_decisions(1))
    estimate_plates(JPEG, [JPEG], "T1")
    assert openai_api.client_kwargs["timeout"] == 30.0
