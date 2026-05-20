#!/usr/bin/env bash
# Probe four assistant-replay payload shapes against two Responses-compatible
# providers (OpenAI, OpenRouter). Goal: find a single spec-correct shape that
# works on both, with the prior turn's text actually visible to the model on
# turn 2 (not just an HTTP 200).
#
# Each variant sends the same two-turn conversation:
#   turn 1 user:      "My favorite color is purple. Please remember this."
#   turn 1 assistant: "Got it — your favorite color is purple."   (hardcoded, replayed)
#   turn 2 user:      "What is my favorite color? Reply with just one word."
#
# Pass criteria: HTTP 200 AND the model's turn-2 answer contains "purple".
# A 200 without "purple" means the assistant turn was structurally accepted
# but the model didn't see the content — that's a silent context loss and
# disqualifies the shape.
#
# Usage:
#   export OPENAI_API_KEY=...
#   export OPENROUTER_API_KEY=...
#   export DO_API_KEY=...                            # optional; if unset, DO probe is skipped
#   export XAI_API_KEY=...                           # optional; if unset, xAI probe is skipped
#   bash scripts/responses_replay_probe.sh
#
# Optional overrides:
#   OPENAI_MODEL=gpt-4.1-nano (default)
#   OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct (default)
#   DO_MODEL_OS=gemma-4-31B-it (default; the known-quirky cell)
#   XAI_MODEL (required if XAI_API_KEY set; check https://docs.x.ai for current model ids)

set -uo pipefail

: "${OPENAI_API_KEY:?OPENAI_API_KEY must be set}"
: "${OPENROUTER_API_KEY:?OPENROUTER_API_KEY must be set}"
OPENAI_MODEL="${OPENAI_MODEL:-gpt-4.1-nano}"
OPENROUTER_MODEL="${OPENROUTER_MODEL:-meta-llama/llama-3.3-70b-instruct}"
DO_MODEL_OS="${DO_MODEL_OS:-qwen3.5-397b-a17b}"

ASSIST="Got it — your favorite color is purple."
USER1="My favorite color is purple. Please remember this."
USER2="What is my favorite color? Reply with just one word."

# ANSI helpers
hr() { printf '\n──── %s ────\n' "$1"; }

# Run one variant against one endpoint.
# Args: label, endpoint_url, auth_header_value, payload_json
run_test() {
  local label="$1"
  local url="$2"
  local auth="$3"
  local payload="$4"
  local response
  response=$(curl -sS -X POST "$url" \
    -H "Authorization: Bearer $auth" \
    -H "Content-Type: application/json" \
    -d "$payload")

  # Compact summary: did it 200, did the model say "purple", and the raw answer.
  echo "$response" | jq -c --arg label "$label" '
    if .error then
      {label: $label, http: "FAIL", error: (.error.message // .error)}
    else
      ((.output // []) | map(select(.type=="message")) | .[0].content // []) as $c
      | ($c | map(select(.type=="output_text")) | .[0].text // null) as $answer
      | {
          label: $label,
          http: "OK",
          context_ok: (($answer // "") | ascii_downcase | contains("purple")),
          answer: $answer
        }
    end'
}

probe_provider() {
  local pname="$1" url="$2" auth="$3" model="$4"

  hr "Provider: $pname  Model: $model"

  # A. EasyInputMessage + output_text + annotations:[]  (OpenAI-protocol-correct per its error message)
  A=$(jq -nc --arg m "$model" --arg t "$ASSIST" --arg u1 "$USER1" --arg u2 "$USER2" '{
    model: $m,
    input: [
      {role:"user", content:$u1},
      {role:"assistant", content:[{type:"output_text", text:$t, annotations:[]}]},
      {role:"user", content:$u2}
    ]
  }')
  run_test "A. output_text + annotations:[]" "$url" "$auth" "$A"

  # B. EasyInputMessage + input_text  (vLLM-friendly, rejected by OpenAI)
  B=$(jq -nc --arg m "$model" --arg t "$ASSIST" --arg u1 "$USER1" --arg u2 "$USER2" '{
    model: $m,
    input: [
      {role:"user", content:$u1},
      {role:"assistant", content:[{type:"input_text", text:$t}]},
      {role:"user", content:$u2}
    ]
  }')
  run_test "B. input_text" "$url" "$auth" "$B"

  # C. Full OutputItem (type:"message" + id + status + output_text + annotations:[])
  C=$(jq -nc --arg m "$model" --arg t "$ASSIST" --arg u1 "$USER1" --arg u2 "$USER2" '{
    model: $m,
    input: [
      {role:"user", content:$u1},
      {
        type:"message",
        id:"msg_probe_001",
        role:"assistant",
        status:"completed",
        content:[{type:"output_text", text:$t, annotations:[]}]
      },
      {role:"user", content:$u2}
    ]
  }')
  run_test "C. full OutputItem" "$url" "$auth" "$C"

  # D. Bare-string content shortcut
  D=$(jq -nc --arg m "$model" --arg t "$ASSIST" --arg u1 "$USER1" --arg u2 "$USER2" '{
    model: $m,
    input: [
      {role:"user", content:$u1},
      {role:"assistant", content:$t},
      {role:"user", content:$u2}
    ]
  }')
  run_test "D. bare string" "$url" "$auth" "$D"
}

probe_provider "OpenAI"     "https://api.openai.com/v1/responses"     "$OPENAI_API_KEY"     "$OPENAI_MODEL"
probe_provider "OpenRouter" "https://openrouter.ai/api/v1/responses"  "$OPENROUTER_API_KEY" "$OPENROUTER_MODEL"

if [[ -n "${DO_API_KEY:-}" ]]; then
  probe_provider "DigitalOcean (OS)" "https://inference.do-ai.run/v1/responses" "$DO_API_KEY" "$DO_MODEL_OS"
else
  echo
  echo "(skipping DigitalOcean — set DO_API_KEY to include)"
fi

if [[ -n "${XAI_API_KEY:-}" ]]; then
  if [[ -z "${XAI_MODEL:-}" ]]; then
    echo
    echo "ERROR: XAI_API_KEY is set but XAI_MODEL is not. Set XAI_MODEL=<grok-id> and re-run."
  else
    probe_provider "xAI" "https://api.x.ai/v1/responses" "$XAI_API_KEY" "$XAI_MODEL"
  fi
else
  echo
  echo "(skipping xAI — set XAI_API_KEY and XAI_MODEL to include)"
fi

echo
echo 'Pick the variant whose row reads {http:"OK", context_ok:true} for ALL probed providers.'
