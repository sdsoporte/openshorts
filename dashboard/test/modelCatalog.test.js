// Classification of a provider's /models catalog for the model dropdown.
// NaN (api.nan.builders) lists 18 ids that include chat models, embeddings,
// rerank, TTS, STT and two image models. Before this, every one of them landed
// in "unknown" and the auto-select took "the first not blocked", which on an
// aggregator can be a model that cannot answer a chat completion at all.
import test from "node:test";
import assert from "node:assert/strict";

import { classifyModel } from "../src/modelCatalog.js";

// Verbatim ids from GET https://api.nan.builders/v1/models (2026-11-15).
const NAN_CHAT = [
  "deepseek-v4-flash", "deepseek-v4-pro", "gemma4", "glm5.3", "glm5.3-flash",
  "hy4", "kimi-k3.1-pro", "mimo-v2.6-flash", "qwen3.6", "qwen3.8-flash",
  "qwen3.8-max", "minimax-h3",
];
const NAN_NOT_CHAT = [
  "qwen3-embedding", "rerank", "kokoro", "whisper", "flux-2-klein",
  "qwen-image-2.1",
];

test("every NaN chat model is recommended", () => {
  for (const id of NAN_CHAT) {
    assert.equal(classifyModel(id), "recommended", `${id} should be recommended`);
  }
});

test("every NaN non-chat model is blocked", () => {
  for (const id of NAN_NOT_CHAT) {
    assert.equal(classifyModel(id), "blocked", `${id} should be blocked`);
  }
});

test("an embedding id that also matches a chat family stays blocked", () => {
  // qwen3-embedding prefix-matches the qwen3 chat family: the block list has
  // to win, or the auto-select can land on a model with no chat endpoint.
  assert.equal(classifyModel("qwen3-embedding"), "blocked");
});

test("the NVIDIA-style catalog still classifies as before", () => {
  assert.equal(classifyModel("nvidia/llama-3.1-nemotron-70b-instruct"), "recommended");
  assert.equal(classifyModel("meta/llama-3.1-70b-instruct"), "recommended");
  assert.equal(classifyModel("nvidia/nv-embed-v1"), "blocked");
  assert.equal(classifyModel("nvidia/cosmos-1.0"), "blocked");
});

test("an id that matches nothing is still unknown", () => {
  assert.equal(classifyModel("my-own-export-2024"), "unknown");
});
