"""Retry behaviour of the Gemini scoring/detail stage.

Prod (22-jul-2026) lost 3 jobs to Gemini answering 200 with an empty body.
That raises while parsing, not while calling, so it used to escape the retry
loop and kill the job on the first blip.
"""
import types
import pytest

# main pulls in cv2/torch/mediapipe at import time; the minimal CI env lacks
# them, so skip there. Runs fully in the container/local where deps exist.
main = pytest.importorskip("main")


class _FakeResponse:
    def __init__(self, parsed=None):
        self.parsed = parsed
        self.candidates = []
        self.usage_metadata = None

    @property
    def text(self):
        return "" if self.parsed is None else "{}"


class _FakeModels:
    """Returns an empty body for the first `blips` calls, then a good one."""

    def __init__(self, blips, payload=None):
        self.blips = blips
        self.calls = 0
        self.payload = payload if payload is not None else {"windows": [{"id": "w0", "score": 90}]}

    def generate_content(self, **kwargs):
        self.calls += 1
        if self.calls <= self.blips:
            return _FakeResponse(parsed=None)
        return _FakeResponse(parsed=self.payload)


def _client(models):
    return types.SimpleNamespace(models=models)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)


def test_recovers_from_a_single_empty_body(monkeypatch):
    models = _FakeModels(blips=1)
    parsed, _cost = main._run_gemini_stage(_client(models), "m", "prompt", object)
    assert models.calls == 2
    assert parsed["windows"][0]["score"] == 90


def test_recovers_from_two_consecutive_blips():
    models = _FakeModels(blips=2)
    parsed, _cost = main._run_gemini_stage(_client(models), "m", "prompt", object)
    assert models.calls == 3
    assert parsed["windows"]


def test_gives_up_after_three_attempts():
    models = _FakeModels(blips=99)
    with pytest.raises(Exception) as exc:
        main._run_gemini_stage(_client(models), "m", "prompt", object)
    assert models.calls == 3
    assert "empty response body" in str(exc.value)


def test_non_transient_errors_are_not_retried():
    class _Boom:
        calls = 0

        def generate_content(self, **kwargs):
            _Boom.calls += 1
            raise ValueError("400 INVALID_ARGUMENT: bad request")

    with pytest.raises(ValueError):
        main._run_gemini_stage(_client(_Boom()), "m", "prompt", object)
    assert _Boom.calls == 1


def test_succeeds_without_retrying_when_the_first_call_is_fine():
    models = _FakeModels(blips=0)
    main._run_gemini_stage(_client(models), "m", "prompt", object)
    assert models.calls == 1


class _BlockedResponse:
    """Mimics the real prod shape: 200, empty candidates, block_reason set."""
    text = ""
    candidates = []
    usage_metadata = None

    class _PF:
        class _Reason:
            name = "PROHIBITED_CONTENT"
        block_reason = _Reason()

    prompt_feedback = _PF()


def test_policy_block_fails_fast_without_retrying():
    # Prod 23-jul-2026: PROHIBITED_CONTENT is deterministic — 3 retries just
    # burned quota and reported a misleading "empty response body".
    class _Models:
        calls = 0

        def generate_content(self, **kwargs):
            _Models.calls += 1
            return _BlockedResponse()

    import gemini_worker
    with pytest.raises(gemini_worker.GeminiBlockedError) as exc:
        main._run_gemini_stage(_client(_Models()), "m", "prompt", object)
    assert _Models.calls == 1
    assert "PROHIBITED_CONTENT" in str(exc.value)


def test_blocked_finish_reason_also_raises():
    import gemini_worker

    class _Cand:
        class _FR:
            name = "SAFETY"
        finish_reason = _FR()
        content = None

    class _Resp:
        prompt_feedback = None
        candidates = [_Cand()]

    with pytest.raises(gemini_worker.GeminiBlockedError):
        gemini_worker.raise_if_blocked(_Resp())


def test_clean_response_is_not_flagged_as_blocked():
    import gemini_worker

    class _Resp:
        prompt_feedback = None
        candidates = []

    gemini_worker.raise_if_blocked(_Resp())  # must not raise


# --- OpenAI-compatible provider (NaN, vLLM, Ollama behind a proxy) ----------
# The retry loop is shared, but the failure codes are not: NaN closes a
# non-streamed request at 120 s with a 524 instead of hanging, and a 402
# ("insufficient_balance") is a billing wall that retrying only delays.

def _llm_client(monkeypatch, failures):
    """Drive the LLM branch with `failures` exceptions, then a good answer."""
    import llm_backend
    monkeypatch.setattr(llm_backend, "active", lambda: True)
    state = {"calls": 0}

    def _generate_json(prompt, schema, model=None):
        state["calls"] += 1
        if state["calls"] <= len(failures):
            raise failures[state["calls"] - 1]
        return {"windows": [{"id": "w0", "score": 90}]}, None

    monkeypatch.setattr(llm_backend, "generate_json", _generate_json)
    return state


def test_retries_a_524_from_the_provider(monkeypatch):
    """NaN answers 524 when a non-streamed call passes 120 s."""
    state = _llm_client(monkeypatch, [
        RuntimeError("LLM server 524 from https://api.nan.builders/v1/chat/completions: "
                     "<html><body>A timeout occurred</body></html>"),
    ])
    parsed, _cost = main._run_gemini_stage(None, "m", "prompt", object)
    assert state["calls"] == 2
    assert parsed["windows"][0]["score"] == 90


def test_does_not_retry_a_402_insufficient_balance(monkeypatch):
    """A billing wall is not transient: retrying only burns time."""
    state = _llm_client(monkeypatch, [
        RuntimeError('LLM server 402 from https://api.nan.builders/v1/chat/completions: '
                     '{"error":{"type":"insufficient_balance"}}'),
    ])
    with pytest.raises(RuntimeError):
        main._run_gemini_stage(None, "m", "prompt", object)
    assert state["calls"] == 1
