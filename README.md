# whisperpocket

Local voice assistant for Apple Silicon. Wake word → STT → LLM → TTS, all on-device. No API keys, no cloud, no cost.

```
"Hey pal, what time is it?"

Mic → VAD → Whisper STT → wake word → LLM → PocketTTS → speaker
         (one Python process, no daemons, no HTTP)
```

## Install

```bash
git clone https://github.com/mgalpert/whisperpocket.git && cd whisperpocket
make install
```

Requires macOS 14+ on Apple Silicon, [uv](https://docs.astral.sh/uv/) (`brew install uv`), Python 3.10+.

## Usage

```bash
wp listen --wake pal --wake "hey pal"
```

That's it. First run downloads models (~500MB total), then it's fully offline.

Say your wake word followed by a question. It transcribes your speech, sends it to an LLM, and speaks the response back — with ASMR typing sounds while it thinks.

| Option | Default | Description |
|--------|---------|-------------|
| `--wake WORD` | *(required)* | Wake word (repeatable) |
| `--llm COMMAND` | `openclaw agent --agent main --message` | LLM command |
| `--model MODEL` | `distil-small.en` | Whisper model |
| `--voice VOICE` | `alba` | TTS voice |
| `--energy-threshold N` | `-35` | dBFS gate |
| `--no-typing` | | Disable typing sounds |
| `--verbose` | | Debug output |

**Interrupting:** Say "stop", "shush", "shut up", "quiet", or "enough" — or press Escape.

**LLM backend:** Uses [openclaw](https://docs.openclaw.ai) by default, but any CLI that takes text as its last argument and prints a response works. Pass `--llm "your-command"`.

## TTS Only

`wp` also works as a standalone TTS tool:

```bash
wp "Hello world"               # speak text (auto-starts daemon)
wp "Hello world" -o out.wav    # write to file
wp serve                       # run daemon in foreground
wp stop                        # stop daemon
wp warmup                      # pre-load models
```

## How It Works

1. **Capture** — `sounddevice` records 16kHz mono audio
2. **VAD** — WebRTC VAD (aggressiveness 2) detects speech segments
3. **STT** — [Lightning Whisper MLX](https://github.com/mustafaaljadery/lightning-whisper-mlx) transcribes in-process on the GPU
4. **Wake word** — Punctuation-tolerant matching ("Hey, pal!" matches "hey pal")
5. **LLM** — Runs your LLM command as a subprocess, plays typing sounds while waiting
6. **Chunking** — Splits response into sentences, strips markdown
7. **TTS** — [PocketTTS](https://github.com/kyutai-labs/pockettts) synthesizes in-process, pipelined (next chunk synthesizes while current plays)
8. **Playback** — `sounddevice` plays 24kHz float32 audio

## License

MIT — see [LICENSE](LICENSE).

PocketTTS models: Apache 2.0 (Kyutai Labs). Whisper models: MIT (OpenAI).
