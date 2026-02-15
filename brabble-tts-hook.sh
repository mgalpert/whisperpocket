#!/bin/bash
# brabble hook: ASMR typing while LLM works + pipelined TTS response
#
# Called as: brabble-tts-hook.sh "<spoken text>"
#
# Env vars (set by brabble or override manually):
#   BRABPOCKET       path to brabpocket binary (default: ~/.local/bin/brabpocket)
#   LLM_COMMAND      LLM command to run (default: openclaw agent --agent main --message)
#   TYPING_AUDIO     path to typing sound WAV (default: <script_dir>/Resources/typing.wav)

TEXT="$1"
if [ -z "$TEXT" ]; then
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BRABPOCKET="${BRABPOCKET:-${HOME}/.local/bin/brabpocket}"
LLM_COMMAND="${LLM_COMMAND:-/opt/homebrew/bin/openclaw agent --agent main --message}"
TYPING_AUDIO="${TYPING_AUDIO:-${SCRIPT_DIR}/Resources/typing.wav}"
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo "[brabble-tts] Q: $TEXT"

# Start ASMR keyboard typing in background (loop it)
if [ -f "$TYPING_AUDIO" ]; then
  (
    trap 'kill $(jobs -p) 2>/dev/null; exit' TERM
    while true; do
      afplay "$TYPING_AUDIO" &
      wait $!
    done
  ) &
  TYPING_PID=$!
fi

# Call LLM while typing plays
RESPONSE=$($LLM_COMMAND "$TEXT" 2>/dev/null)

# Stop the typing sounds (kills loop + current afplay)
if [ -n "$TYPING_PID" ]; then
  kill -- -$TYPING_PID 2>/dev/null || kill $TYPING_PID 2>/dev/null
  pkill -P $TYPING_PID 2>/dev/null
  wait $TYPING_PID 2>/dev/null
fi

if [ -z "$RESPONSE" ]; then
  say "Sorry, I didn't get a response."
  exit 0
fi

echo "[brabble-tts] A: $RESPONSE"

# Split into chunks and write to numbered files
python3 -c "
import sys, re, os

text = sys.stdin.read()

text = re.sub(r'\*\*', '', text)
text = re.sub(r'\*', '', text)
text = re.sub(r'\`[^\`]*\`', '', text)
text = re.sub(r'\`', '', text)
text = re.sub(r'https?://\S+', '', text)

lines = text.split('\n')
chunks = []
buf = []

def flush():
    t = ' '.join(buf).strip()
    t = re.sub(r'\s+', ' ', t)
    if len(t) >= 3:
        chunks.append(t)
    buf.clear()

for line in lines:
    stripped = line.strip()
    if not stripped:
        flush()
        continue
    if re.match(r'^#+\s', stripped):
        flush()
        heading = re.sub(r'^#+\s+', '', stripped).strip()
        if len(heading) >= 3:
            chunks.append(heading)
        continue
    if re.match(r'^[-•*]\s', stripped):
        flush()
        item = re.sub(r'^[-•*]\s+', '', stripped).strip()
        if len(item) >= 3:
            chunks.append(item)
        continue
    if re.match(r'^\d+[.)]\s', stripped):
        flush()
        item = re.sub(r'^\d+[.)]\s+', '', stripped).strip()
        if len(item) >= 3:
            chunks.append(item)
        continue
    buf.append(stripped)

flush()

final = []
for chunk in chunks:
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z])', chunk)
    for p in parts:
        p = p.strip()
        if len(p) >= 3:
            final.append(p)

tmpdir = sys.argv[1]
for i, c in enumerate(final):
    with open(os.path.join(tmpdir, f'{i:04d}.txt'), 'w') as f:
        f.write(c)
print(len(final))
" "$TMPDIR" <<< "$RESPONSE"

COUNT=$(ls "$TMPDIR"/*.txt 2>/dev/null | wc -l | tr -d ' ')
if [ "$COUNT" -eq 0 ]; then
  exit 0
fi

# Pipelined TTS: synthesize ahead while playing current chunk.
# Synthesize first chunk immediately (blocking — need it before we can play).
FIRST_TXT=$(cat "$TMPDIR/0000.txt")
"$BRABPOCKET" "$FIRST_TXT" -o "$TMPDIR/0000.wav"

# Start synthesizing second chunk in background
if [ -f "$TMPDIR/0001.txt" ]; then
  "$BRABPOCKET" "$(cat "$TMPDIR/0001.txt")" -o "$TMPDIR/0001.wav" &
  SYNTH_PID=$!
fi

# Play first chunk
afplay "$TMPDIR/0000.wav"

# Process remaining chunks: play current (already synthesized), synth next
i=1
while [ -f "$TMPDIR/$(printf '%04d' $i).txt" ]; do
  # Wait for current chunk synthesis to finish
  if [ -n "$SYNTH_PID" ]; then
    wait $SYNTH_PID 2>/dev/null
  fi

  NEXT=$((i + 1))
  NEXT_FILE="$TMPDIR/$(printf '%04d' $NEXT).txt"

  # Start synthesizing next chunk in background while we play current
  if [ -f "$NEXT_FILE" ]; then
    "$BRABPOCKET" "$(cat "$NEXT_FILE")" -o "$TMPDIR/$(printf '%04d' $NEXT).wav" &
    SYNTH_PID=$!
  else
    SYNTH_PID=""
  fi

  # Play current chunk
  afplay "$TMPDIR/$(printf '%04d' $i).wav"

  i=$((i + 1))
done
