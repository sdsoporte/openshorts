# NaN (api.nan.builders) como proveedor OpenAI-compatible — Clip Generator

Make the Clip Generator stop depending on Gemini for its text passes, and open the
path for its frame stages. Provider: `https://api.nan.builders/v1`, OpenAI-compatible,
verified live (18 models reachable by the configured key).

## Specs

**S1.** The provider must be usable from the Clip Generator mode: `deepseek-v4-flash`
for both transcript passes, with `LLM_SCORE_BATCH=8` (the 1M-context models do not
need the 3-window local-LLM default).

**S2.** The driver is Google quota, not credits. User's words: "no es para 'ahorar' es
por que tengo mallor disponibilidad de token en NaN que en la sub Google AI Pro". So
record runtime cost is irrelevant; reducing Gemini calls is what counts.

**S3.** Scope is the Clip Generator: "afectaria al modo Clip Generator de OpenShort,
que es lo que mas me importa por el momento".

**S4.** `524` must be retried. NaN closes non-streamed requests at 120 s with a `524`,
and `main._run_gemini_stage` does not list it, so a slow reasoning call kills the job.

**S5.** Reasoning must be controllable per job: `LLM_REASONING_EFFORT` is sent on
`/chat/completions` when set. NaN's GLM models cannot turn reasoning off (`none` is
applied as `minimal`); `deepseek-v4-flash` and `mimo` can.

**S6.** The model dropdown must classify an aggregator catalog. NaN's `/v1/models`
returns 18 ids including `qwen3-embedding`, `rerank`, `kokoro`, `whisper`,
`flux-2-klein`, `qwen-image-2.1`; today every one of them lands in "unknown" and the
auto-select can pick a non-chat model.

**S7.** A vision path must exist for the frame stages (`burned_subtitles` first): an
image goes to `/v1/chat/completions` as an `image_url` data URL with the same
`response_format` fallback and pydantic validation as the text path. Verified by probe:
one 768px frame → `{"layout":"single_speaker",...}` in 2.7 s, 796 tokens.

**S8.** `burned_subtitles` uses the vision path only when the env flag is on; without
it, Gemini behaves exactly as today.

**S9.** No existing behavior changes for a self-host without the new env vars: Gemini
remains the default for every stage.

**Out of scope / blocked:** re-measuring `layout_picker` needs the 48-clip corpus with
hand labels, which is not in this repository (`docs/research/` holds only two market
docs). The measurement is a gate for switching the picker in production, so the picker
is not switched here.

## Tasks

| ID | Spec | Route | Commit |
|---|---|---|---|
| T1 | S4 | inline + test | `fix(clip-selection): retry 524 from OpenAI-compatible servers` |
| T2 | S5 | inline + test | `feat(llm): send reasoning_effort when configured` |
| T3 | S6 | inline + lint/build | `fix(dashboard): classify aggregator model catalogs` |
| T4 | S1,S2 | inline + docs | `docs: document NaN provider, batch size and reasoning` |
| T5 | S7 | inline + test | `feat(llm): vision path for frame stages` |
| T6 | S8 | inline + test | `feat(subtitles): detect burned-in text through the vision path` |
| T7 | — | blocked | corpus labels not available |

## Log

**L1** (user, verbatim): "ok, procedamos con el plan compelto."

**L2** (user, verbatim): "no es para 'ahorar' es por que tengo mallor disponibilidad
de token en NaN que en la sub Google AI Pro. me es mas rentable los tokens de NaN."

**L3** (evidence, 2026-11-15): `GET /v1/usage` on the configured key — 7.137 requests,
1.327M prompt tokens, 393.2 credits in one month; `deepseek-v4-flash` effective
0.29 cr/1M vs 10.5 list. Credits are not the constraint; 60 rpm / 7 concurrent are.

**L4** (evidence): both `gemma4` and `deepseek-v4-flash` answered our exact request
body (`json_schema` + `reasoning_effort: none`) with schema-compliant JSON in 1.6-2.5 s.

**L5** (evidence): `nan-media` MCP exposes 8 tools, all verified except the two image
ones (100/month quota). It offers no image-understanding tool; vision lives in
`/chat/completions`.
