# OpenAI-compatible providers (verified against NaN)

The Clip Generator's two transcript passes (score → detail) can run on any
OpenAI-compatible `/chat/completions` server instead of Gemini. This page
records what was verified against a real provider and how to configure it.

## What moves off Gemini, and what does not

| Stage | Needs | Provider |
|---|---|---|
| Scoring (transcript windows → scores) | text | **configurable** |
| Detail (shortlist → hooks, titles, captions) | text | **configurable** |
| Burned-in subtitle detection (12 frames) | vision | Gemini today |
| Layout picker (12 frames, `AUTO_LAYOUT=1`) | vision | Gemini today |
| Screencast / wide detection, hook grounding | vision | Gemini today |
| Transcription | local word timestamps | local faster-whisper |

A video is 4 text calls plus 1-3 vision calls; the text half is the token-heavy
half, so moving it removes almost all Gemini tokens while the vision calls stay
small (12 frames each).

## Verified provider: NaN (api.nan.builders)

- Base URL `https://api.nan.builders/v1`, key starts with `sk-`.
- `GET /v1/models` is filtered by what the key can actually call. 18 ids were
  reachable on the account used for the verification.
- `/chat/completions` accepts `json_schema` (validated, re-served when it
  misses) and `reasoning_effort`; images go in as `image_url` data URLs.
- Limits: 60 rpm, 7 concurrent requests per key (10 on the premium plan),
  per-model concurrency 5-7, and `glm5.3` also has a 400M token / rolling 4h
  window. A non-streamed request over 120 s is closed with `524`.
- 1 credit ≈ €0.026 at 1.000-credit packs. Measured on a real account over a
  month: 1.327M prompt tokens cost 393 credits, i.e. `deepseek-v4-flash` bills
  at ~0.29 credits per 1M rather than the 10.5 list price.

### Configuration

```sh
LLM_BASE_URL=https://api.nan.builders/v1
LLM_MODEL=deepseek-v4-flash
LLM_API_KEY=sk-...
LLM_SCORE_BATCH=8
LLM_REASONING_EFFORT=none
```

- `LLM_SCORE_BATCH=8`: the default for a compatible endpoint is 3, which
  assumes a local model with a 4-8k context. Hosted models have 1M, so 8 is
  both cheaper and the difference between fitting the 60 rpm limit and
  colliding with it on a busy queue.
- `LLM_REASONING_EFFORT=none`: scoring and detail are closed JSON decisions.
  GLM models cannot turn reasoning off (`none` is applied as `minimal`);
  `deepseek-v4-flash` and `mimo-v2.6-flash` can. An unrecognised value is
  dropped instead of sent, because servers answer `400` and `400` is not
  retried.

### Which model, for which pass

| Model | Use | Note |
|---|---|---|
| `deepseek-v4-flash` | both passes | 1M context, reasoning off, strict `json_schema` on chat, and the best image model on the cluster if a vision stage is moved later |
| `gemma4` | scoring | does not draw credits at all; 262K context; strict `json_schema` |
| `mimo-v2.6-flash` | scoring | cheapest published typical rate; reasoning off; omnimodal |
| `glm5.3` | detail | the quality step for creative text; **text only** — an image in the request answers 400 |
| `deepseek-v4-pro` | detail | larger reasoning model, also text only |
| avoid | — | `kimi-k3.1-pro` and `qwen3.8-max` bill output at 300-500 credits per 1M for a closed JSON task |

## Failure codes worth knowing

| Code | Meaning | Behaviour |
|---|---|---|
| `400` | invalid field (e.g. a bad `reasoning_effort`) | not retried, by design |
| `402` | `insufficient_balance` | not retried: a billing wall is not transient |
| `429` | rate limit or the `glm5.3` rolling window | retried with backoff |
| `502` / `504` | upstream trouble | retried |
| `524` | non-streamed request passed 120 s | retried |

## What the MCP servers do (and do not) provide

NaN ships two MCP bridges for agents. Neither replaces a pipeline dependency:

- `nan-search` (`https://api.nan.builders/mcp`) answered `initialize` but
  `tools/list` returned an empty list for the key used here, across three
  protocol versions.
- `nan-media` (`npx -y nan-mcp-server@1.1.2`) exposes 8 tools: image
  generation/editing (flux-2-klein, 100 images/month quota), TTS (kokoro, 67
  voices including three Spanish), STT (whisper), embeddings and rerank.
  `speech_to_text` returns **segments, not word timings** (`words: null`), so it
  cannot replace local transcription — the subtitle path needs per-word
  alignment. `embed_text` returns a summary, not the vectors.
  There is no image-understanding tool: vision lives in `/chat/completions`.
