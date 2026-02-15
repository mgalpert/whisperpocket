# brabpocket

Fast local text-to-speech CLI for Apple Silicon. Runs [PocketTTS](https://github.com/kyutai-labs/pocket-tts) (155M params) via PyTorch on Apple Silicon — no API keys, no cloud, no cost.

## Requirements

- macOS 14+ on Apple Silicon
- [uv](https://docs.astral.sh/uv/) (`brew install uv`)

## Quick Start

```bash
make install

# Speak (auto-starts daemon on first use)
brabpocket "Hello world"

# First run downloads models (~400MB), subsequent runs are instant (~350ms)
```

## Usage

```bash
brabpocket "Hello world"               # speak text (auto-starts daemon)
brabpocket "Hello world" -o out.wav    # write to file instead
brabpocket serve                       # run daemon in foreground
brabpocket status                      # check daemon status
brabpocket stop                        # stop daemon
brabpocket warmup                      # pre-load models
```

## Always-On Daemon (launchd)

```bash
make install-daemon    # install + enable launchd service
make uninstall-daemon  # remove launchd service
```

## How It Works

**Daemon + client** — `pocket-tts serve` keeps models warm in memory:

- First call auto-starts the daemon (~5s model loading)
- Subsequent calls hit the warm daemon via HTTP
- ~200MB RSS once loaded

### Latency

| Text Length | Synthesis Time | vs ElevenLabs |
|------------|---------------|---------------|
| Single word | ~350ms | 2x faster |
| Short sentence | ~500ms | 2-3x faster |
| Long sentence | ~1.3s | comparable |

All fully local — no network, no API key, no cost.

## License

MIT — see [LICENSE](LICENSE).

PocketTTS models: Apache 2.0 (Kyutai Labs).
