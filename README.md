# brabpocket

Local voice pipeline for Apple Silicon: **STT** (MLX Whisper) + **TTS** (PocketTTS). No API keys, no cloud, no cost.

Designed to work with [brabble](https://github.com/steipete/brabble) (wake-word voice daemon), but each piece runs standalone.

## Components

| Component | What it does | Port |
|-----------|-------------|------|
| `brabpocket` | TTS CLI + daemon (PocketTTS, 155M params) | 8111 |
| `stt-server/` | STT daemon (MLX Whisper on Apple Silicon GPU) | 8112 |
| `brabble-tts-hook.sh` | Glue script: LLM query → sentence chunking → pipelined TTS | — |

## Requirements

- macOS 14+ on Apple Silicon
- [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- Python 3.10+

## Quick Start

### TTS only

```bash
make install

# Speak (auto-starts daemon on first use)
brabpocket "Hello world"

# First run downloads models (~400MB), subsequent runs are instant (~350ms)
```

### STT only

```bash
cd stt-server
uv run python server.py serve --port 8112 --model distil-small.en

# Test with a WAV file
ffmpeg -i test.wav -f s16le -ar 16000 -ac 1 - | \
  curl -s -X POST http://localhost:8112/transcribe \
    --data-binary @- -H 'Content-Type: application/octet-stream'
```

### Full pipeline with brabble

Set your brabble config (`~/.config/brabble/config.toml`):

```toml
[asr]
backend = "mlx"
model_name = "distil-small.en"
stt_port = 8112

[[hooks]]
wake = ["pal", "hey pal"]
command = "/path/to/brabpocket/brabble-tts-hook.sh"
timeout_sec = 180
```

Then `brabble start` — speak your wake word, brabble transcribes via the STT daemon, runs the hook (which queries your LLM and speaks the response via brabpocket).

## TTS Usage

```bash
brabpocket "Hello world"               # speak text (auto-starts daemon)
brabpocket "Hello world" -o out.wav    # write to file instead
brabpocket serve                       # run daemon in foreground
brabpocket status                      # check daemon status
brabpocket stop                        # stop daemon
brabpocket warmup                      # pre-load models
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

## Hook Script

`brabble-tts-hook.sh` is the glue between STT and TTS. When brabble hears speech:

1. Plays ASMR keyboard typing sounds while waiting
2. Sends transcribed text to your LLM (configurable via `LLM_COMMAND` env var)
3. Splits the response into sentences/chunks (strips markdown)
4. Synthesizes + plays chunks with 1-ahead pipelining (next chunk synthesizes while current plays)

**Environment variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `BRABPOCKET` | `~/.local/bin/brabpocket` | Path to TTS binary |
| `LLM_COMMAND` | `openclaw agent --agent main --message` | LLM command (text appended as arg) |
| `TYPING_AUDIO` | `<script_dir>/Resources/typing.wav` | Typing sound effect |

## How It Works

```
Mic → VAD → MLX Whisper STT (port 8112) → wake word check
  → hook script → LLM → sentence chunking
  → PocketTTS (port 8111) → pipelined afplay → speaker
```

Both daemons stay warm in memory (~200MB TTS + ~300MB STT) for fast repeated inference. Brabble suppresses ASR while the hook is running to prevent TTS feedback loops, with a 2-second grace period for residual audio decay.

## License

MIT — see [LICENSE](LICENSE).

PocketTTS models: Apache 2.0 (Kyutai Labs). Whisper models: MIT (OpenAI).
