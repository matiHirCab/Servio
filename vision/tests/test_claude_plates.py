"""Tests de Claude por plato con el proveedor mockeado (TOM-3)."""

import json
import subprocess

import pytest

from servio_vision.classifiers import claude_plates
from servio_vision.classifiers.claude_plates import ClaudeCallError, estimate_plates

JPEG = b"\xff\xd8fake"


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

    def fake(content, model, timeout_s):
        calls.append({"content": content, "model": model, "timeout_s": timeout_s})
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


def test_retries_once_then_succeeds(backend):
    calls, answers = backend
    answers += [ClaudeCallError("timeout"), _answer(2)]
    assert estimate_plates(JPEG, [JPEG] * 2, "T1") is not None
    assert len(calls) == 2


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
        assert json.loads(kwargs["input"])["message"]["content"][1:] == ["x"]
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(claude_plates, "_find_cli", lambda: "claude")
    monkeypatch.setattr(claude_plates.subprocess, "run", fake_run)
    data, usage = claude_plates._call_cli(["x"], "haiku", 10)
    assert data == _answer(1)
    assert usage["cost_usd"] == 0.001


def test_cli_timeout_is_a_call_error(monkeypatch):
    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 10)

    monkeypatch.setattr(claude_plates, "_find_cli", lambda: "claude")
    monkeypatch.setattr(claude_plates.subprocess, "run", fake_run)
    with pytest.raises(ClaudeCallError):
        claude_plates._call_cli(["x"], "haiku", 10)


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
    data, _ = claude_plates._call_cli([{"type": "text", "text": "x"}], "haiku", 10)
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
    data, _ = claude_plates._call_cli(["x"], "haiku", 10)
    assert data == _answer(1)


def test_cli_error_without_json_is_a_call_error(monkeypatch):
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="se cortó")

    monkeypatch.setattr(claude_plates, "_find_cli", lambda: "claude")
    monkeypatch.setattr(claude_plates.subprocess, "run", fake_run)
    with pytest.raises(ClaudeCallError, match="se cortó"):
        claude_plates._call_cli(["x"], "haiku", 10)
