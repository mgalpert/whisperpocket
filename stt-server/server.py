"""MLX Whisper STT daemon.

Accepts raw PCM int16 LE audio via HTTP POST and returns transcribed text.
Keeps the model warm in GPU memory between requests.
"""

from __future__ import annotations

import argparse
import sys
import time
from contextlib import asynccontextmanager

import numpy as np
import uvicorn
from fastapi import FastAPI, Request, Response

_whisper = None
_model_name: str = ""


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _whisper, _model_name
    from lightning_whisper_mlx import LightningWhisperMLX

    model = app.state.model_name
    _model_name = model
    _whisper = LightningWhisperMLX(model=model, batch_size=12, quant=None)
    # warm up with a short silence buffer
    silence = np.zeros(16000, dtype=np.float32)
    _whisper.transcribe(silence)
    yield
    _whisper = None


app = FastAPI(lifespan=_lifespan)


@app.post("/transcribe")
async def transcribe(request: Request):
    body = await request.body()
    if len(body) == 0:
        return {"text": "", "duration_ms": 0}
    pcm16 = np.frombuffer(body, dtype=np.int16)
    samples = pcm16.astype(np.float32) / 32768.0
    t0 = time.monotonic()
    result = _whisper.transcribe(samples)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    text = result.get("text", "").strip() if isinstance(result, dict) else str(result).strip()
    return {"text": text, "duration_ms": elapsed_ms}


@app.get("/health")
async def health():
    return {"status": "ok", "model": _model_name}


def cli():
    parser = argparse.ArgumentParser(prog="wp-stt")
    sub = parser.add_subparsers(dest="command")
    serve_parser = sub.add_parser("serve", help="Start STT server")
    serve_parser.add_argument("--port", type=int, default=8112)
    serve_parser.add_argument("--model", default="distil-small.en")
    args = parser.parse_args()
    if args.command != "serve":
        parser.print_help()
        sys.exit(1)
    app.state.model_name = args.model
    uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1, log_level="warning")


if __name__ == "__main__":
    cli()
