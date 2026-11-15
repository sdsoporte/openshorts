// Classify a provider's /models catalog for the self-host model picker.
//
// The dropdown used to be tuned to a single catalog (NVIDIA NIM), where every
// chat model carries "instruct" or "chat" in its id and everything else was
// noise. An aggregator lists one flat namespace instead: NaN
// (api.nan.builders) returns 18 ids that mix chat models with embeddings,
// rerank, TTS, STT and two image models. With the old patterns every one of
// those landed in "unknown", and the auto-select then took "the first not
// blocked", which on an aggregator can be a model with no chat endpoint.
//
// Two rules, in this order:
//   1. the block list wins, because a specialised id can prefix-match a chat
//      family (`qwen3-embedding` starts with the qwen3 chat prefix);
//   2. a chat family is recommended, so the auto-select prefers something that
//      can actually answer /chat/completions.
// An id that matches neither stays "unknown": that is the honest answer, and
// the dropdown still offers it.

const BLOCK_PATTERNS = [
  // non-chat modalities and tooling
  /embed/i, /reward/i, /guard/i, /safety/i, /parse/i, /rerank/i,
  /vision/i, /vlm/i, /vl-/i, /audio/i, /translate/i, /tts/i, /asr/i,
  /whisper/i, /kokoro/i, /speech/i, /transcri/i,
  // image generation and editing
  /diffusion/i, /flux/i, /image/i, /sdxl/i, /sd3/i, /dall-?e/i, /upscal/i,
  // detectors and classifiers that answer a narrower shape than we ask for
  /cosmos/i, /vila/i, /neva/i, /clip/i, /detector/i, /calibration/i,
  /ising/i, /retriever/i, /qa$/i,
];

const CHAT_PATTERNS = [
  // generic instruct / chat naming
  /instruct$/i, /chat$/i, /instruct-/i, /chat-/i,
  /nemotron.*instruct/i, /mistral.*instruct/i, /llama.*instruct/i,
  /command.*r$/i, /zephyr/i, /vicuna/i, /orca/i, /wizard/i, /openchat/i,
  /starling/i, /tulu/i,
  // families served by aggregators, whose ids carry no "chat"/"instruct" word
  /deepseek/i, /glm/i, /gemma/i, /kimi/i, /mimo/i, /minimax/i,
  /qwen[0-9.]/i, /hy[0-9]/i,
];

/** "blocked" | "recommended" | "unknown" for one model id. */
export function classifyModel(id) {
  const value = String(id ?? "");
  if (!value) return "unknown";
  if (BLOCK_PATTERNS.some((re) => re.test(value))) return "blocked";
  if (CHAT_PATTERNS.some((re) => re.test(value))) return "recommended";
  return "unknown";
}

export { BLOCK_PATTERNS, CHAT_PATTERNS };
