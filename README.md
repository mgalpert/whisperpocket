# whisperpocket

Local voice pipeline for Apple Silicon: **STT** (MLX Whisper) + **TTS** (PocketTTS). No API keys, no cloud, no cost.

## Components

| Component | What it does | Port |
|-----------|-------------|------|
| `wp` | TTS CLI + daemon (PocketTTS, 155M params) | 8111 |
| `wp listen` | Full voice pipeline: mic → VAD → STT → wake word → LLM → TTS | — |
| `stt-server/` | Standalone STT daemon (MLX Whisper on Apple Silicon GPU) | 8112 |
| `wp-hook.sh` | Glue script for brabble: LLM query → sentence chunking → pipelined TTS | — |

## Requirements

- macOS 14+ on Apple Silicon
- [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- Python 3.10+

For the full voice pipeline you also need:
- [openclaw](https://docs.openclaw.ai) — LLM gateway (or any CLI that takes a text argument and prints a response)

## Quick Start

### TTS only

```bash
make install

# Speak (auto-starts daemon on first use)
wp "Hello world"

# First run downloads models (~400MB), subsequent runs are instant (~350ms)
```

### Voice pipeline (`wp listen`)

One process replaces the entire brabble + stt-server + hook script with a single voice pipeline: audio capture → VAD → STT → wake word → LLM → chunked TTS → playback.

```bash
# Install
git clone https://github.com/mgalpert/whisperpocket.git
cd whisperpocket
make install

# Start listening
wp listen --wake pal --wake "hey pal"

# Say "hey pal, what time is it?" and listen
```

**Options:**

```
wp listen [options]
  --wake WORD          Wake word (repeatable). Required.
  --llm COMMAND        LLM command (default: openclaw agent --agent main --message)
  --model MODEL        Whisper model (default: distil-small.en)
  --voice VOICE        TTS voice (default: alba)
  --energy-threshold N dBFS gate (default: -35)
  --no-typing          Disable typing sounds
  --verbose            Debug output
```

To stop TTS mid-response, say "stop", "shush", "shut up", "quiet", or "enough".

### STT server (standalone)

```bash
cd stt-server
uv run python server.py serve --port 8112 --model distil-small.en

# Test with a WAV file
ffmpeg -i test.wav -f s16le -ar 16000 -ac 1 - | \
  curl -s -X POST http://localhost:8112/transcribe \
    --data-binary @- -H 'Content-Type: application/octet-stream'
```

## TTS Usage

```bash
wp "Hello world"               # speak text (auto-starts daemon)
wp "Hello world" -o out.wav    # write to file instead
wp serve                       # run daemon in foreground
wp status                      # check daemon status
wp stop                        # stop daemon
wp warmup                      # pre-load models
```

### Always-On Daemon (launchd)

```bash
make install-daemon    # install + enable launchd service
make uninstall-daemon  # remove launchd service
```

### TTS Latency

| Text Length | Synthesis Time | vs ElevenLabs |
|------------|---------------|---------------|
| Single word | ~350ms | 2x faster |
| Short sentence | ~500ms | 2-3x faster |
| Long sentence | ~1.3s | comparable |

## STT Server

The `stt-server/` directory is a FastAPI app that loads [lightning-whisper-mlx](https://github.com/mustafaaljadery/lightning-whisper-mlx) and keeps it warm in GPU memory.

**Endpoints:**
- `POST /transcribe` — raw PCM int16 LE body → `{"text": "...", "duration_ms": N}`
- `GET /health` — `{"status": "ok", "model": "..."}`

**Available models:** `distil-small.en` (fastest), `distil-medium.en`, `distil-large-v3`, `large-v3-turbo`. Models download automatically on first use.

```bash
cd stt-server
uv run python server.py serve --port 8112 --model distil-small.en
```

## Hook Script (brabble integration)

`wp-hook.sh` is the glue between STT and TTS when used with [brabble](https://github.com/steipete/brabble). When brabble hears speech:

1. Plays ASMR keyboard typing sounds while waiting
2. Sends transcribed text to your LLM (configurable via `LLM_COMMAND` env var)
3. Splits the response into sentences/chunks (strips markdown)
4. Synthesizes + plays chunks with 1-ahead pipelining (next chunk synthesizes while current plays)

**Environment variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `WP` | `~/.local/bin/wp` | Path to TTS binary |
| `LLM_COMMAND` | `openclaw agent --agent main --message` | LLM command (text appended as last arg) |
| `TYPING_AUDIO` | `<script_dir>/Resources/typing.wav` | Typing sound effect |

## How It Works

### `wp listen` (recommended)

```
Mic → VAD → MLX Whisper STT (in-process) → wake word check
  → LLM subprocess → sentence chunking
  → PocketTTS (in-process) → pipelined playback → speaker
```

Single Python process, no HTTP boundaries, no separate daemons.

### brabble + hook (legacy)

```
Mic → VAD → MLX Whisper STT (port 8112) → wake word check
  → hook script → LLM → sentence chunking
  → PocketTTS (port 8111) → pipelined afplay → speaker
```

Both STT and TTS stay warm in memory (~200MB TTS + ~300MB STT) for fast repeated inference.

## License

MIT — see [LICENSE](LICENSE).

PocketTTS models: Apache 2.0 (Kyutai Labs). Whisper models: MIT (OpenAI).
