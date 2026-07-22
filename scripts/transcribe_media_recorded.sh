#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_MODEL="fun-asr"
DEFAULT_OBJECT_PREFIX="asr-temp/cutpoint-lab"
DEFAULT_OUTPUT_DIR=""
INPUT_PATH=""
OUTPUT_PATH=""
TASK_HOTWORDS=""
BASE_VOCABULARY_ID=""
USE_BASE_VOCABULARY=0
NO_BASE_VOCABULARY=0
MODEL="$DEFAULT_MODEL"
POLL_INTERVAL=10
WAIT_TIMEOUT=3600
KEEP_OSS=0
KEEP_VOCABULARY=0
YES=0
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage:
  transcribe_media_recorded.sh --input /path/to/audio-or-video [options]

Options:
  --input <path>                 Audio or video file to transcribe.
  --output <path>                Markdown output path. Default: input basename + .md.
  --output-dir <dir>             Directory for run artifacts. Default: output file directory.
  --task-hotwords <csv>          Task-specific hotwords, comma-separated.
  --base-vocabulary-id <id>      Base DashScope vocabulary ID to merge. Default: none
                                 (or ASR_BASE_VOCABULARY_ID from the environment).
  --no-base-vocabulary           Do not merge a base vocabulary (overrides env).
  --model <name>                 Recorded ASR model. Default: fun-asr.
  --poll-interval <seconds>      Task polling interval. Default: 10.
  --wait-timeout <seconds>       Max wait for DashScope task. Default: 3600.
  --keep-oss                     Keep temporary OSS object after success.
  --keep-vocabulary              Keep temporary merged vocabulary after success.
  --yes                          Non-interactive; use provided/default hotword settings.
  --dry-run                      Validate inputs and print planned config without network calls.
  -h, --help                     Show this help.

Environment:
  Sources <repo root>/.env when present (see .env.example).
  Requires DASHSCOPE_API_KEY, OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET,
  OSS_BUCKET, OSS_ENDPOINT, ffmpeg, jq, curl, and python3.
  Optional: ASR_BASE_VOCABULARY_ID to merge a base hotword vocabulary.
EOF
}

die() {
  echo "[asr] ERROR: $*" >&2
  exit 1
}

log() {
  echo "[asr] $*" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input)
      INPUT_PATH="${2:-}"; shift 2 ;;
    --output)
      OUTPUT_PATH="${2:-}"; shift 2 ;;
    --output-dir)
      DEFAULT_OUTPUT_DIR="${2:-}"; shift 2 ;;
    --task-hotwords)
      TASK_HOTWORDS="${2:-}"; shift 2 ;;
    --base-vocabulary-id)
      BASE_VOCABULARY_ID="${2:-}"; USE_BASE_VOCABULARY=1; shift 2 ;;
    --no-base-vocabulary)
      USE_BASE_VOCABULARY=0; BASE_VOCABULARY_ID=""; NO_BASE_VOCABULARY=1; shift ;;
    --model)
      MODEL="${2:-}"; shift 2 ;;
    --poll-interval)
      POLL_INTERVAL="${2:-}"; shift 2 ;;
    --wait-timeout)
      WAIT_TIMEOUT="${2:-}"; shift 2 ;;
    --keep-oss)
      KEEP_OSS=1; shift ;;
    --keep-vocabulary)
      KEEP_VOCABULARY=1; shift ;;
    --yes)
      YES=1; shift ;;
    --dry-run)
      DRY_RUN=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      die "unknown argument: $1" ;;
  esac
done

[[ -n "$INPUT_PATH" ]] || { usage; exit 2; }
[[ -f "$INPUT_PATH" ]] || die "input file not found: $INPUT_PATH"

set -a
[[ -f "$ROOT_DIR/.env" ]] && source "$ROOT_DIR/.env"
set +a

if [[ "$USE_BASE_VOCABULARY" -eq 0 && "$NO_BASE_VOCABULARY" -eq 0 && -n "${ASR_BASE_VOCABULARY_ID:-}" ]]; then
  BASE_VOCABULARY_ID="$ASR_BASE_VOCABULARY_ID"
  USE_BASE_VOCABULARY=1
fi

for cmd in curl jq python3 ffmpeg; do
  command -v "$cmd" >/dev/null 2>&1 || die "$cmd not found"
done

[[ -n "${DASHSCOPE_API_KEY:-}" ]] || die "DASHSCOPE_API_KEY is required"
[[ -n "${OSS_ACCESS_KEY_ID:-}" ]] || die "OSS_ACCESS_KEY_ID is required"
[[ -n "${OSS_ACCESS_KEY_SECRET:-}" ]] || die "OSS_ACCESS_KEY_SECRET is required"
[[ -n "${OSS_BUCKET:-}" ]] || die "OSS_BUCKET is required"
[[ -n "${OSS_ENDPOINT:-}" ]] || die "OSS_ENDPOINT is required"

INPUT_ABS="$(cd "$(dirname "$INPUT_PATH")" && pwd)/$(basename "$INPUT_PATH")"
INPUT_DIR="$(dirname "$INPUT_ABS")"
INPUT_STEM="$(basename "$INPUT_ABS")"
INPUT_STEM="${INPUT_STEM%.*}"
if [[ -z "$OUTPUT_PATH" ]]; then
  OUTPUT_PATH="$INPUT_DIR/$INPUT_STEM.md"
fi
OUTPUT_ABS="$(cd "$(dirname "$OUTPUT_PATH")" && pwd)/$(basename "$OUTPUT_PATH")"
OUTPUT_DIR="$(dirname "$OUTPUT_ABS")"
if [[ -z "$DEFAULT_OUTPUT_DIR" ]]; then
  RUN_PARENT="$OUTPUT_DIR"
else
  mkdir -p "$DEFAULT_OUTPUT_DIR"
  RUN_PARENT="$(cd "$DEFAULT_OUTPUT_DIR" && pwd)"
fi

if [[ "$YES" -ne 1 ]]; then
  echo "[asr] Hotword confirmation"
  if [[ "$USE_BASE_VOCABULARY" -eq 1 ]]; then
    read -r -p "Use base vocabulary $BASE_VOCABULARY_ID? [Y/n] " reply
    case "$reply" in
      n|N|no|NO|No)
        USE_BASE_VOCABULARY=0
        BASE_VOCABULARY_ID=""
        ;;
    esac
  fi
  if [[ -z "$TASK_HOTWORDS" ]]; then
    read -r -p "Task hotwords, comma-separated (empty allowed): " TASK_HOTWORDS
  else
    echo "[asr] Task hotwords: $TASK_HOTWORDS"
  fi
  echo "[asr] Model: $MODEL"
  read -r -p "Start ASR with these hotword settings? [y/N] " confirm
  case "$confirm" in
    y|Y|yes|YES|Yes) ;;
    *) die "cancelled" ;;
  esac
fi

RUN_ID="$(date '+%Y%m%d-%H%M%S')"
RUN_DIR="$RUN_PARENT/asr-recorded-$RUN_ID"
MEDIA_PATH="$RUN_DIR/input.m4a"
BASE_VOCAB_PATH="$RUN_DIR/base-vocabulary.json"
CREATE_VOCAB_REQUEST="$RUN_DIR/create-vocabulary-request.json"
CREATE_VOCAB_RESPONSE="$RUN_DIR/create-vocabulary-response.json"
SUBMIT_REQUEST="$RUN_DIR/dashscope-submit-request.json"
SUBMIT_RESPONSE="$RUN_DIR/dashscope-submit-response.json"
TASK_RESPONSE="$RUN_DIR/dashscope-task.json"
TRANSCRIPT_JSON="$RUN_DIR/dashscope-transcript.json"
SUMMARY_JSON="$RUN_DIR/summary.json"
mkdir -p "$RUN_DIR"

if [[ "$DRY_RUN" -eq 1 ]]; then
  jq -n \
    --arg input "$INPUT_ABS" \
    --arg output "$OUTPUT_ABS" \
    --arg runDir "$RUN_DIR" \
    --arg model "$MODEL" \
    --arg baseVocabularyId "$BASE_VOCABULARY_ID" \
    --arg taskHotwords "$TASK_HOTWORDS" \
    '{dryRun:true,input:$input,output:$output,runDir:$runDir,model:$model,baseVocabularyId:$baseVocabularyId,taskHotwords:$taskHotwords}'
  exit 0
fi

log "converting media to m4a: $MEDIA_PATH"
ffmpeg -y -i "$INPUT_ABS" -vn -c:a aac -b:a 96k "$MEDIA_PATH" >/dev/null 2>"$RUN_DIR/ffmpeg.log"

query_vocabulary() {
  local vocabulary_id="$1"
  jq -n --arg id "$vocabulary_id" \
    '{model:"speech-biasing",input:{action:"query_vocabulary",vocabulary_id:$id},parameters:{}}' |
    curl -sS https://dashscope.aliyuncs.com/api/v1/services/audio/asr/customization \
      -H "Authorization: Bearer ${DASHSCOPE_API_KEY}" \
      -H "Content-Type: application/json" \
      --data-binary @-
}

create_vocabulary_payload() {
  python3 - "$BASE_VOCAB_PATH" "$TASK_HOTWORDS" "$MODEL" <<'PY'
import json
import re
import sys
from pathlib import Path

base_path, task_hotwords, model = sys.argv[1:]
words = []
if Path(base_path).exists() and Path(base_path).stat().st_size > 0:
    payload = json.loads(Path(base_path).read_text(encoding="utf-8"))
    for item in payload.get("output", {}).get("vocabulary", []):
        if not isinstance(item, dict):
            continue
        item = dict(item)
        item.pop("target_lang", None)
        if str(item.get("text", "")).strip():
            words.append(item)

for raw in re.split(r"[,，\n]", task_hotwords or ""):
    text = raw.strip()
    if text:
        words.append({"lang": "zh", "text": text, "weight": 5})

dedup = {}
for item in words:
    text = str(item.get("text", "")).strip()
    if not text:
        continue
    item["text"] = text
    item["weight"] = int(item.get("weight") or 5)
    dedup[text] = item

prefix = "asr" + __import__("datetime").datetime.now().strftime("%m%d%H%M")
print(json.dumps({
    "model": "speech-biasing",
    "input": {
        "action": "create_vocabulary",
        "prefix": prefix[:10],
        "target_model": model,
        "vocabulary": list(dedup.values()),
    },
    "parameters": {},
}, ensure_ascii=False))
PY
}

TEMP_VOCABULARY_ID=""
if [[ "$USE_BASE_VOCABULARY" -eq 1 && -n "$BASE_VOCABULARY_ID" ]]; then
  log "querying base vocabulary: $BASE_VOCABULARY_ID"
  query_vocabulary "$BASE_VOCABULARY_ID" > "$BASE_VOCAB_PATH"
  jq -e '.output.vocabulary | length >= 0' "$BASE_VOCAB_PATH" >/dev/null || die "failed to query base vocabulary"
else
  : > "$BASE_VOCAB_PATH"
fi

if [[ "$USE_BASE_VOCABULARY" -eq 1 || -n "$TASK_HOTWORDS" ]]; then
  create_vocabulary_payload > "$CREATE_VOCAB_REQUEST"
  log "creating temporary merged vocabulary"
  curl -sS https://dashscope.aliyuncs.com/api/v1/services/audio/asr/customization \
    -H "Authorization: Bearer ${DASHSCOPE_API_KEY}" \
    -H "Content-Type: application/json" \
    --data-binary @"$CREATE_VOCAB_REQUEST" > "$CREATE_VOCAB_RESPONSE"
  TEMP_VOCABULARY_ID="$(jq -r '.output.vocabulary_id // empty' "$CREATE_VOCAB_RESPONSE")"
  [[ -n "$TEMP_VOCABULARY_ID" ]] || die "failed to create vocabulary: $(cat "$CREATE_VOCAB_RESPONSE")"
  log "temporary vocabulary: $TEMP_VOCABULARY_ID"
fi

sign_oss_url() {
  local method="$1"
  local object_key="$2"
  local expires_seconds="$3"
  local content_type="${4:-}"
  METHOD="$method" OBJECT_KEY="$object_key" EXPIRES_SECONDS="$expires_seconds" CONTENT_TYPE="$content_type" python3 - <<'PY'
import base64
import hashlib
import hmac
import os
import time
import urllib.parse

method = os.environ["METHOD"]
bucket = os.environ["OSS_BUCKET"]
endpoint = os.environ["OSS_ENDPOINT"].removeprefix("https://").removeprefix("http://").rstrip("/")
object_key = os.environ["OBJECT_KEY"].strip("/")
content_type = os.environ.get("CONTENT_TYPE", "")
expires = str(int(time.time()) + int(os.environ["EXPIRES_SECONDS"]))
resource = f"/{bucket}/{object_key}"
string_to_sign = f"{method}\n\n{content_type}\n{expires}\n{resource}"
signature = base64.b64encode(
    hmac.new(os.environ["OSS_ACCESS_KEY_SECRET"].encode(), string_to_sign.encode(), hashlib.sha1).digest()
).decode()
path = "/".join(urllib.parse.quote(part) for part in object_key.split("/"))
query = urllib.parse.urlencode({
    "OSSAccessKeyId": os.environ["OSS_ACCESS_KEY_ID"],
    "Expires": expires,
    "Signature": signature,
})
print(f"https://{bucket}.{endpoint}/{path}?{query}")
PY
}

OBJECT_DATE="$(date '+%Y/%m/%d')"
SAFE_STEM="$(python3 - "$INPUT_STEM" <<'PY'
import re
import sys
stem = re.sub(r"[^A-Za-z0-9._-]+", "-", sys.argv[1]).strip("-._")
print((stem or "media")[:80])
PY
)"
OBJECT_KEY="$DEFAULT_OBJECT_PREFIX/$OBJECT_DATE/${SAFE_STEM}-${RUN_ID}.m4a"
CONTENT_TYPE="audio/mp4"
PUT_URL="$(sign_oss_url PUT "$OBJECT_KEY" 3600 "$CONTENT_TYPE")"
GET_URL="$(sign_oss_url GET "$OBJECT_KEY" 86400)"

log "uploading temporary audio to OSS: $OBJECT_KEY"
curl -fS -X PUT "$PUT_URL" -H "Content-Type: $CONTENT_TYPE" --data-binary @"$MEDIA_PATH" > "$RUN_DIR/oss-upload-response.txt"

jq -n \
  --arg url "$GET_URL" \
  --arg model "$MODEL" \
  --arg vocabularyId "$TEMP_VOCABULARY_ID" \
  '{
    model:$model,
    input:{file_urls:[$url]},
    parameters: (if $vocabularyId == "" then {} else {vocabulary_id:$vocabularyId} end)
  }' > "$SUBMIT_REQUEST"

log "submitting DashScope recorded ASR task"
curl -sS -X POST https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription \
  -H "Authorization: Bearer ${DASHSCOPE_API_KEY}" \
  -H "Content-Type: application/json" \
  -H "X-DashScope-Async: enable" \
  --data-binary @"$SUBMIT_REQUEST" > "$SUBMIT_RESPONSE"

TASK_ID="$(jq -r '.output.task_id // empty' "$SUBMIT_RESPONSE")"
[[ -n "$TASK_ID" ]] || die "failed to submit ASR task: $(cat "$SUBMIT_RESPONSE")"
log "DashScope task: $TASK_ID"

deadline=$((SECONDS + WAIT_TIMEOUT))
while true; do
  curl -sS -X POST "https://dashscope.aliyuncs.com/api/v1/tasks/${TASK_ID}" \
    -H "Authorization: Bearer ${DASHSCOPE_API_KEY}" \
    -H "Content-Type: application/json" > "$TASK_RESPONSE"
  TASK_STATUS="$(jq -r '.output.task_status // "UNKNOWN"' "$TASK_RESPONSE")"
  log "task status: $TASK_STATUS"
  case "$TASK_STATUS" in
    SUCCEEDED) break ;;
    FAILED|CANCELED|UNKNOWN)
      die "ASR task ended with status $TASK_STATUS: $(cat "$TASK_RESPONSE")" ;;
  esac
  if (( SECONDS >= deadline )); then
    die "timed out waiting for ASR task after ${WAIT_TIMEOUT}s"
  fi
  sleep "$POLL_INTERVAL"
done

TRANSCRIPTION_URL="$(jq -r '.output.results[0].transcription_url // empty' "$TASK_RESPONSE")"
[[ -n "$TRANSCRIPTION_URL" ]] || die "missing transcription_url: $(cat "$TASK_RESPONSE")"
log "downloading transcription JSON"
curl -fS "$TRANSCRIPTION_URL" -o "$TRANSCRIPT_JSON"
jq -r '.transcripts[0].text // empty' "$TRANSCRIPT_JSON" > "$OUTPUT_ABS"

TEXT_CHARS="$(wc -m < "$OUTPUT_ABS" | tr -d ' ')"
SENTENCES="$(jq -r '.transcripts[0].sentences | length' "$TRANSCRIPT_JSON")"
DURATION_MS="$(jq -r '.transcripts[0].content_duration_in_milliseconds // 0' "$TRANSCRIPT_JSON")"

OSS_DELETED=0
if [[ "$KEEP_OSS" -ne 1 ]]; then
  log "deleting temporary OSS object"
  DELETE_URL="$(sign_oss_url DELETE "$OBJECT_KEY" 3600)"
  if curl -fsS -X DELETE "$DELETE_URL" > "$RUN_DIR/oss-delete-response.txt"; then
    OSS_DELETED=1
  else
    log "warning: failed to delete OSS object; lifecycle rules may still remove it"
  fi
fi

VOCABULARY_DELETED=0
if [[ "$KEEP_VOCABULARY" -ne 1 && -n "$TEMP_VOCABULARY_ID" ]]; then
  log "deleting temporary vocabulary"
  jq -n --arg id "$TEMP_VOCABULARY_ID" \
    '{model:"speech-biasing",input:{action:"delete_vocabulary",vocabulary_id:$id},parameters:{}}' |
    curl -sS https://dashscope.aliyuncs.com/api/v1/services/audio/asr/customization \
      -H "Authorization: Bearer ${DASHSCOPE_API_KEY}" \
      -H "Content-Type: application/json" \
      --data-binary @- > "$RUN_DIR/delete-vocabulary-response.json" || true
  if jq -e '(.code // "") == ""' "$RUN_DIR/delete-vocabulary-response.json" >/dev/null; then
    VOCABULARY_DELETED=1
  else
    log "warning: failed to delete temporary vocabulary: $(cat "$RUN_DIR/delete-vocabulary-response.json")"
  fi
fi

jq -n \
  --arg input "$INPUT_ABS" \
  --arg output "$OUTPUT_ABS" \
  --arg runDir "$RUN_DIR" \
  --arg media "$MEDIA_PATH" \
  --arg model "$MODEL" \
  --arg baseVocabularyId "$BASE_VOCABULARY_ID" \
  --arg temporaryVocabularyId "$TEMP_VOCABULARY_ID" \
  --arg taskHotwords "$TASK_HOTWORDS" \
  --arg taskId "$TASK_ID" \
  --arg objectKey "$OBJECT_KEY" \
  --argjson ossDeleted "$OSS_DELETED" \
  --argjson vocabularyDeleted "$VOCABULARY_DELETED" \
  --argjson textChars "$TEXT_CHARS" \
  --argjson sentences "$SENTENCES" \
  --argjson durationMs "$DURATION_MS" \
  '{
    input:$input,
    output:$output,
    runDir:$runDir,
    convertedAudio:$media,
    model:$model,
    baseVocabularyId:$baseVocabularyId,
    temporaryVocabularyId:$temporaryVocabularyId,
    taskHotwords:$taskHotwords,
    taskId:$taskId,
    ossObjectKey:$objectKey,
    ossDeleted:$ossDeleted,
    vocabularyDeleted:$vocabularyDeleted,
    durationMs:$durationMs,
    sentences:$sentences,
    textChars:$textChars
  }' > "$SUMMARY_JSON"

log "done"
echo "$OUTPUT_ABS"
echo "$SUMMARY_JSON"
