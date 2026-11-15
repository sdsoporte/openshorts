"""The OpenAI-compatible moment-picker backend (llm_backend.py).

Self-hosters asked for a pipeline that never calls Google. The transcript
passes go to any /chat/completions server when LLM_BASE_URL is set; these
tests pin the contract main.py relies on: same (parsed, cost) shape as the
Gemini stage, schema validation, and the response_format fallback ladder.
"""
import json

import httpx
import pytest

import gemini_worker
import llm_backend


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
    monkeypatch.setenv("LLM_MODEL", "qwen2.5:14b")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)


def _serve(handler, monkeypatch):
    """Route llm_backend's httpx client through an in-process handler."""
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(llm_backend, "_client",
                        lambda **kw: httpx.Client(transport=transport, **kw))


def _completion(payload, usage=None):
    return httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": json.dumps(payload)}}],
        "usage": usage or {"prompt_tokens": 120, "completion_tokens": 40},
    })


# --- activation -----------------------------------------------------------

def test_inactive_without_a_base_url(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert llm_backend.active() is False
    assert llm_backend.describe() is None


def test_base_url_alone_activates_and_describes(local):
    assert llm_backend.active() is True
    assert llm_backend.describe() == {
        "provider": "openai", "model": "qwen2.5:14b", "baseUrl": "http://llm.test/v1"}


def test_explicit_gemini_provider_wins_over_base_url(local, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    assert llm_backend.active() is False


# --- generate_json ---------------------------------------------------------

def test_returns_validated_payload_and_zero_cost(local, monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return _completion({"windows": [{"id": "w0", "start": 0, "end": 90, "score": 88, "reason": "hook"}]})

    _serve(handler, monkeypatch)
    parsed, cost = llm_backend.generate_json("prompt", gemini_worker.ScoreResponse)

    assert seen["url"] == "http://llm.test/v1/chat/completions"
    assert seen["body"]["model"] == "qwen2.5:14b"
    assert seen["body"]["response_format"]["type"] == "json_schema"
    assert seen["auth"] == "Bearer ollama"  # documented placeholder when no key is set
    assert parsed["windows"][0]["score"] == 88
    assert cost["total_cost"] == 0.0 and cost["local"] is True
    assert cost["input_tokens"] == 120 and cost["output_tokens"] == 40


def test_falls_back_to_json_object_when_schema_mode_is_rejected(local, monkeypatch):
    formats = []

    def handler(request):
        body = json.loads(request.content)
        fmt = (body.get("response_format") or {}).get("type")
        formats.append(fmt)
        if fmt == "json_schema":
            return httpx.Response(400, json={"error": {"message": "response_format json_schema not supported"}})
        return _completion({"windows": []})

    _serve(handler, monkeypatch)
    parsed, _ = llm_backend.generate_json("prompt", gemini_worker.ScoreResponse)
    assert formats == ["json_schema", "json_object"]
    assert parsed == {"windows": []}


def test_code_fenced_json_is_accepted(local, monkeypatch):
    """Small models wrap the object in ```json fences even when told not to."""
    def handler(request):
        text = "```json\n" + json.dumps({"windows": []}) + "\n```"
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}], "usage": {}})

    _serve(handler, monkeypatch)
    parsed, _ = llm_backend.generate_json("prompt", gemini_worker.ScoreResponse)
    assert parsed == {"windows": []}


def test_schema_violation_raises_instead_of_leaking_into_the_pipeline(local, monkeypatch):
    _serve(lambda r: _completion({"windows": [{"id": "w0"}]}), monkeypatch)
    with pytest.raises(Exception) as exc:
        llm_backend.generate_json("prompt", gemini_worker.ScoreResponse)
    assert "validation error" in str(exc.value)


def test_server_errors_surface_with_status_and_url(local, monkeypatch):
    _serve(lambda r: httpx.Response(503, text="loading model"), monkeypatch)
    with pytest.raises(RuntimeError) as exc:
        llm_backend.generate_json("prompt", gemini_worker.ScoreResponse)
    assert "503" in str(exc.value) and "loading model" in str(exc.value)


# --- main.py routing (needs the heavy deps; skipped on minimal CI) ------------

def test_stage_routes_to_local_backend_and_retries_transient(local, monkeypatch):
    main = pytest.importorskip("main")
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def fake_generate(prompt, schema, model=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("ConnectError: connection refused")
        return {"windows": [{"id": "w0", "start": 0, "end": 1, "score": 50, "reason": ""}]}, {"total_cost": 0.0}

    monkeypatch.setattr(llm_backend, "generate_json", fake_generate)
    parsed, cost = main._run_gemini_stage(None, "qwen2.5:14b", "prompt", gemini_worker.ScoreResponse)
    assert calls["n"] == 2
    assert parsed["windows"][0]["score"] == 50
    assert cost["total_cost"] == 0.0


def test_score_batch_shrinks_for_local_models(local, monkeypatch):
    main = pytest.importorskip("main")
    monkeypatch.delenv("LLM_SCORE_BATCH", raising=False)
    assert main.score_batch_size() == 3
    monkeypatch.setenv("LLM_SCORE_BATCH", "5")
    assert main.score_batch_size() == 5
    monkeypatch.delenv("LLM_BASE_URL")
    monkeypatch.delenv("LLM_SCORE_BATCH")
    assert main.score_batch_size() == 8


# --- reasoning control ----------------------------------------------------
# NaN always reasons unless told otherwise, and the scoring pass is a closed
# JSON decision where reasoning is pure latency and output credits. GLM models
# cannot turn it off (none is applied as minimal); deepseek-v4-flash can.
# An unsupported value answers 400, which is NOT retried, so it must never
# reach the wire.

def _body_catcher(monkeypatch):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return _completion({"windows": [{"id": "w0", "start": 0, "end": 30,
                                        "score": 90, "reason": "hook"}]})

    _serve(handler, monkeypatch)
    return seen


def test_reasoning_effort_is_sent_when_configured(local, monkeypatch):
    monkeypatch.setenv("LLM_REASONING_EFFORT", "none")
    seen = _body_catcher(monkeypatch)
    llm_backend.generate_json("prompt", gemini_worker.ScoreResponse)
    assert seen["body"]["reasoning_effort"] == "none"


def test_reasoning_effort_is_absent_by_default(local, monkeypatch):
    monkeypatch.delenv("LLM_REASONING_EFFORT", raising=False)
    seen = _body_catcher(monkeypatch)
    llm_backend.generate_json("prompt", gemini_worker.ScoreResponse)
    assert "reasoning_effort" not in seen["body"]


def test_an_unsupported_reasoning_level_never_reaches_the_wire(local, monkeypatch):
    monkeypatch.setenv("LLM_REASONING_EFFORT", "turbo")
    seen = _body_catcher(monkeypatch)
    llm_backend.generate_json("prompt", gemini_worker.ScoreResponse)
    assert "reasoning_effort" not in seen["body"]


def test_describe_reports_the_reasoning_level(local, monkeypatch):
    monkeypatch.setenv("LLM_REASONING_EFFORT", "None")
    assert llm_backend.describe()["reasoningEffort"] == "none"


# --- vision path -----------------------------------------------------------
# The frame stages (burst-in subtitle detection today; the layout picker after
# the upstream merge) need the model to look at pixels. NaN takes images as
# OpenAI-style image_url parts with a data URL, but a text-only server must
# keep receiving the plain string content it has always received.

def test_images_become_openai_image_parts(local, monkeypatch):
    seen = _body_catcher(monkeypatch)
    llm_backend.generate_json("prompt", gemini_worker.ScoreResponse,
                              images=[("image/jpeg", b"\x01\x02"), ("image/png", b"\x03")])
    content = seen["body"]["messages"][-1]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "prompt"}
    parts = content[1:]
    assert len(parts) == 2
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_without_images_the_content_stays_a_plain_string(local, monkeypatch):
    seen = _body_catcher(monkeypatch)
    llm_backend.generate_json("prompt", gemini_worker.ScoreResponse)
    content = seen["body"]["messages"][-1]["content"]
    assert isinstance(content, str) and content == "prompt"


def test_a_non_image_mime_is_rejected_before_the_call(local):
    with pytest.raises(ValueError):
        llm_backend.generate_json("prompt", gemini_worker.ScoreResponse,
                                  images=[("text/plain", b"nope")])


def test_vision_is_off_unless_the_flag_is_set(local, monkeypatch):
    monkeypatch.delenv("LLM_VISION", raising=False)
    assert llm_backend.vision_active() is False
    monkeypatch.setenv("LLM_VISION", "1")
    assert llm_backend.vision_active() is True
    monkeypatch.setenv("LLM_VISION", "0")
    assert llm_backend.vision_active() is False


def test_vision_is_off_without_a_provider(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_VISION", "1")
    assert llm_backend.vision_active() is False
