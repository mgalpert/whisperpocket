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

For the full voice pipeline you also need:
- [brabble](https://github.com/steipete/brabble) — always-on wake-word voice daemon (listens on mic, runs hooks on speech)
- [openclaw](https://docs.openclaw.ai) — LLM gateway (or any CLI that takes a text argument and prints a response)

## Quick Start

### TTS only (no other dependencies)

```bash
make install

# Speak (auto-starts daemon on first use)
brabpocket "Hello world"

# First run downloads models (~400MB), subsequent runs are instant (~350ms)
```

### STT only (no other dependencies)

```bash
cd stt-server
uv run python server.py serve --port 8112 --model distil-small.en

# Test with a WAV file
ffmpeg -i test.wav -f s16le -ar 16000 -ac 1 - | \
  curl -s -X POST http://localhost:8112/transcribe \
    --data-binary @- -H 'Content-Type: application/octet-stream'
```

### Full voice pipeline (STT → LLM → TTS)

This gives you a voice assistant you can talk to. You'll need brabble + an LLM backend.

**1. Install brabpocket (this repo):**

```bash
git clone https://github.com/mgalpert/brabpocket.git
cd brabpocket
make install          # installs brabpocket + brabble-tts-hook.sh to ~/.local/bin
brabpocket warmup     # downloads TTS models (~400MB, one-time)
```

**2. Install brabble** (wake-word daemon — listens on your mic):

```bash
# Clone and build with MLX backend (no whisper.cpp CGO dependency)
git clone https://github.com/steipete/brabble.git
cd brabble
make build-mlx        # builds to bin/brabble
cp bin/brabble ~/.local/bin/
```

**3. Install openclaw** (LLM gateway — routes your voice to an AI agent):

```bash
npm install -g openclaw@latest    # requires Node.js 22+
openclaw onboard --install-daemon  # walks you through API key setup + starts the gateway
```

During onboarding you'll set up an Anthropic API key (recommended) or other provider. openclaw runs a local gateway that manages agent sessions, so your voice conversations have memory and context.

Once onboarded, create an agent:

```bash
openclaw agents create main       # creates the "main" agent
```

The hook script calls `openclaw agent --agent main --message "<your speech>"` by default. The agent responds with text that gets chunked and spoken back via TTS.

> **Don't want openclaw?** Any CLI that takes text as an argument and prints a response works. Set the `LLM_COMMAND` env var — see [Hook Script](#hook-script) below.

**4. Configure brabble** (`~/.config/brabble/config.toml`):

```toml
[asr]
backend = "mlx"
model_name = "distil-small.en"
stt_port = 8112

[wake]
enabled = true
word = "pal"                   # your wake word — say this to activate
aliases = ["hey pal"]

[[hooks]]
wake = ["pal", "hey pal"]
command = "~/.local/bin/brabble-tts-hook.sh"
timeout_sec = 180
min_chars = 8
cooldown_sec = 1
```

**5. Start:**

```bash
brabble start
# Say "hey pal, what's the weather like?" and listen
```

**6. Verify everything works:**

```bash
brabble doctor        # checks mic, STT daemon, hook
brabble status        # uptime + recent transcripts
brabble test-hook "what time is it"   # test the full hook without speaking
```

To stop TTS mid-response, say "stop", "shush", or "shut up" — or run `brabble shush`.

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
| `LLM_COMMAND` | `openclaw agent --agent main --message` | LLM command (text appended as last arg) |
| `TYPING_AUDIO` | `<script_dir>/Resources/typing.wav` | Typing sound effect |

The `LLM_COMMAND` must be a command that accepts the spoken text as its final argument and prints a text response to stdout. To use a different LLM backend, set it in your brabble hook config:

```toml
[[hooks]]
command = "~/.local/bin/brabble-tts-hook.sh"
env = { LLM_COMMAND = "llm -m claude-sonnet-4-5" }
```

Or for a custom script:

```toml
env = { LLM_COMMAND = "/path/to/my-llm-wrapper" }
```

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
