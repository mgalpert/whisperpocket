"""wp listen — single-process voice pipeline: mic → VAD → STT → wake word → LLM → TTS → speaker."""

from __future__ import annotations

import argparse
import os
import random
import re
import select
import signal
import subprocess
import sys
import termios
import threading
import time
import tty
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
import webrtcvad

# ── constants ────────────────────────────────────────────────────────

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 320
SILENCE_MS = 1000
MIN_SPEECH_MS = 300
MAX_SEGMENT_S = 10
ENERGY_THRESHOLD_DBFS = -35
PRE_BUFFER_MS = 200  # audio to keep before VAD triggers
STOP_WORDS = {"stop", "shush", "shut up", "quiet", "enough"}

# ── audio capture with VAD ───────────────────────────────────────────


def energy_dbfs(samples: np.ndarray) -> float:
    """RMS energy in dBFS for int16 samples."""
    rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))
    if rms < 1:
        return -100.0
    return 20 * np.log10(rms / 32768.0)


def capture_speech(
    vad: webrtcvad.Vad,
    energy_threshold: float = ENERGY_THRESHOLD_DBFS,
    verbose: bool = False,
) -> np.ndarray | None:
    """Block until a speech segment is captured. Returns int16 PCM or None on shutdown."""
    silence_frames = SILENCE_MS // FRAME_MS
    min_speech_frames = MIN_SPEECH_MS // FRAME_MS
    max_frames = MAX_SEGMENT_S * 1000 // FRAME_MS

    speech_buf: list[bytes] = []
    silent_count = 0
    speech_count = 0
    recording = False

    q: list[bytes] = []
    event = threading.Event()

    def callback(indata, frames, time_info, status):
        q.append(bytes(indata))
        event.set()

    stream = sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=FRAME_SAMPLES,
        callback=callback,
    )

    with stream:
        while True:
            event.wait()
            event.clear()
            while q:
                frame = q.pop(0)
                samples = np.frombuffer(frame, dtype=np.int16)

                if energy_dbfs(samples) < energy_threshold:
                    if recording:
                        silent_count += 1
                        speech_buf.append(frame)
                        if silent_count >= silence_frames:
                            if speech_count >= min_speech_frames:
                                pcm = np.frombuffer(b"".join(speech_buf), dtype=np.int16)
                                if verbose:
                                    print(f"[wp] captured {len(pcm)/SAMPLE_RATE:.1f}s", file=sys.stderr)
                                return pcm
                            speech_buf.clear()
                            speech_count = 0
                            silent_count = 0
                            recording = False
                    continue

                is_speech = vad.is_speech(frame, SAMPLE_RATE)
                if is_speech:
                    recording = True
                    speech_count += 1
                    silent_count = 0
                    speech_buf.append(frame)
                    if speech_count >= max_frames:
                        pcm = np.frombuffer(b"".join(speech_buf), dtype=np.int16)
                        if verbose:
                            print(f"[wp] max segment {len(pcm)/SAMPLE_RATE:.1f}s", file=sys.stderr)
                        return pcm
                elif recording:
                    silent_count += 1
                    speech_buf.append(frame)
                    if silent_count >= silence_frames:
                        if speech_count >= min_speech_frames:
                            pcm = np.frombuffer(b"".join(speech_buf), dtype=np.int16)
                            if verbose:
                                print(f"[wp] captured {len(pcm)/SAMPLE_RATE:.1f}s", file=sys.stderr)
                            return pcm
                        speech_buf.clear()
                        speech_count = 0
                        silent_count = 0
                        recording = False


# ── wake word matching ───────────────────────────────────────────────


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation between words for wake word matching."""
    return re.sub(r"[,.:;!?\-]+", " ", text.lower()).strip()


def _normalize_tokens(text: str) -> list[str]:
    """Split normalized text into tokens."""
    return _normalize(text).split()


def wake_match(text: str, wake_words: list[str]) -> bool:
    """Check if text starts with any wake word (case-insensitive, punctuation-tolerant)."""
    tokens = _normalize_tokens(text)
    # Sort longest first so "hey pal" matches before "pal"
    for wake in sorted(wake_words, key=len, reverse=True):
        wake_tokens = _normalize(wake).split()
        if tokens[: len(wake_tokens)] == wake_tokens:
            return True
    return False


def strip_wake(text: str, wake_words: list[str]) -> str:
    """Remove wake word prefix from text (punctuation-tolerant)."""
    tokens = _normalize_tokens(text)
    for wake in sorted(wake_words, key=len, reverse=True):
        wake_tokens = _normalize(wake).split()
        if tokens[: len(wake_tokens)] == wake_tokens:
            # Find where the wake word ends in the original text
            # by matching token-by-token through the original
            remaining = text
            for _ in wake_tokens:
                remaining = re.sub(r"^[\s,.:;!?\-]*\S+", "", remaining, count=1)
            return remaining.lstrip(" ,.:;!?-") or text
    return text


# ── sentence chunking ───────────────────────────────────────────────


def split_sentences(text: str) -> list[str]:
    """Split LLM response into speakable chunks, stripping markdown."""
    # Strip markdown formatting
    text = re.sub(r"\*\*", "", text)
    text = re.sub(r"\*", "", text)
    text = re.sub(r"`[^`]*`", "", text)
    text = re.sub(r"`", "", text)
    text = re.sub(r"https?://\S+", "", text)

    lines = text.split("\n")
    chunks: list[str] = []
    buf: list[str] = []

    def flush():
        t = " ".join(buf).strip()
        t = re.sub(r"\s+", " ", t)
        if len(t) >= 3:
            chunks.append(t)
        buf.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if re.match(r"^#+\s", stripped):
            flush()
            heading = re.sub(r"^#+\s+", "", stripped).strip()
            if len(heading) >= 3:
                chunks.append(heading)
            continue
        if re.match(r"^[-•*]\s", stripped):
            flush()
            item = re.sub(r"^[-•*]\s+", "", stripped).strip()
            if len(item) >= 3:
                chunks.append(item)
            continue
        if re.match(r"^\d+[.)]\s", stripped):
            flush()
            item = re.sub(r"^\d+[.)]\s+", "", stripped).strip()
            if len(item) >= 3:
                chunks.append(item)
            continue
        buf.append(stripped)

    flush()

    # Split on sentence boundaries
    final: list[str] = []
    for chunk in chunks:
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", chunk)
        for p in parts:
            p = p.strip()
            if len(p) >= 3:
                final.append(p)

    return final


# ── LLM subprocess ──────────────────────────────────────────────────


def run_llm(llm_cmd: str, text: str) -> str:
    """Run LLM command as subprocess, return response text."""
    try:
        result = subprocess.run(
            llm_cmd.split() + [text],
            capture_output=True,
            text=True,
            timeout=180,
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return ""
    except Exception:
        return ""


# ── typing sounds ────────────────────────────────────────────────────


def _extract_keystrokes(path: str) -> tuple[list[np.ndarray], int] | None:
    """Load typing.wav, detect individual keystrokes, return list of complete key sounds."""
    try:
        with wave.open(path, "rb") as w:
            sr = w.getframerate()
            raw = w.readframes(w.getnframes())
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception:
        return None

    # Detect onsets via amplitude envelope
    win = int(sr * 0.005)  # 5ms analysis window
    env = np.array([np.max(np.abs(samples[i : i + win])) for i in range(0, len(samples) - win, win)])
    threshold = np.max(env) * 0.15
    min_gap = int(0.08 * sr / win)  # 80ms minimum between onsets

    onsets: list[int] = []
    was_below = True
    for i, v in enumerate(env > threshold):
        if v and was_below:
            if not onsets or (i - onsets[-1]) >= min_gap:
                onsets.append(i)
            was_below = False
        elif not v:
            was_below = True

    if not onsets:
        return None

    # Extract each keystroke: onset to onset (or 150ms, whichever is shorter)
    key_max = int(sr * 0.15)
    keys: list[np.ndarray] = []
    for idx, onset in enumerate(onsets):
        start = onset * win
        if idx + 1 < len(onsets):
            end = min(start + key_max, onsets[idx + 1] * win)
        else:
            end = min(start + key_max, len(samples))
        key = samples[start:end]
        # Apply short fade-out to avoid clicks
        fade = min(int(sr * 0.005), len(key))
        key[-fade:] *= np.linspace(1, 0, fade).astype(np.float32)
        keys.append(key)

    return keys, sr


class TypingPlayer:
    """Plays extracted keystroke sounds in word-like bursts with natural pauses."""

    KEY_MIN = 0.03
    KEY_MAX = 0.07
    WORD_LEN = (3, 8)
    SPACE_PAUSE = (0.08, 0.15)
    THINK_PAUSE = (0.25, 0.5)
    THINK_EVERY = (4, 9)

    def __init__(self, keys: list[np.ndarray], sample_rate: int):
        self._keys = keys
        self._sr = sample_rate
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self):
        stream = sd.OutputStream(samplerate=self._sr, channels=1, dtype="float32")
        stream.start()
        words_until_think = random.randint(*self.THINK_EVERY)

        try:
            while not self._stop.is_set():
                word_len = random.randint(*self.WORD_LEN)
                for _ in range(word_len):
                    if self._stop.is_set():
                        return
                    key = random.choice(self._keys)
                    stream.write(key.reshape(-1, 1))
                    if self._stop.wait(random.uniform(self.KEY_MIN, self.KEY_MAX)):
                        return

                words_until_think -= 1
                if words_until_think <= 0:
                    pause = random.uniform(*self.THINK_PAUSE)
                    words_until_think = random.randint(*self.THINK_EVERY)
                else:
                    pause = random.uniform(*self.SPACE_PAUSE)
                if self._stop.wait(pause):
                    return
        finally:
            stream.stop()
            stream.close()


def start_typing(typing_wav: str | None) -> TypingPlayer | None:
    """Start word-cadence typing sounds in background."""
    if not typing_wav or not os.path.exists(typing_wav):
        return None
    result = _extract_keystrokes(typing_wav)
    if not result:
        return None
    player = TypingPlayer(*result)
    player.start()
    return player


def stop_typing(player: TypingPlayer | None):
    """Stop typing sounds."""
    if player is not None:
        player.stop()


# ── stop-word listener during TTS ───────────────────────────────────


class StopWordListener:
    """Lightweight VAD listener that checks for stop words during TTS playback."""

    def __init__(self, whisper, vad: webrtcvad.Vad, verbose: bool = False):
        self._whisper = whisper
        self._vad = vad
        self._verbose = verbose
        self._stopped = threading.Event()
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    def start(self):
        self._stopped.clear()
        self._running = True
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
            self._thread = None

    def _listen(self):
        q: list[bytes] = []
        event = threading.Event()

        def callback(indata, frames, time_info, status):
            q.append(bytes(indata))
            event.set()

        try:
            stream = sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=FRAME_SAMPLES,
                callback=callback,
            )
        except Exception:
            return

        speech_frames: list[bytes] = []
        speech_count = 0
        silent_count = 0
        silence_frames = 500 // FRAME_MS  # 500ms silence for stop words

        with stream:
            while self._running and not self._stopped.is_set():
                event.wait(timeout=0.1)
                event.clear()
                while q and self._running:
                    frame = q.pop(0)
                    is_speech = self._vad.is_speech(frame, SAMPLE_RATE)
                    if is_speech:
                        speech_frames.append(frame)
                        speech_count += 1
                        silent_count = 0
                    elif speech_count > 0:
                        silent_count += 1
                        speech_frames.append(frame)
                        if silent_count >= silence_frames and speech_count >= 3:
                            # Quick transcription check
                            pcm = np.frombuffer(b"".join(speech_frames), dtype=np.int16)
                            audio = pcm.astype(np.float32) / 32768.0
                            try:
                                result = self._whisper.transcribe(audio)
                                text = result.get("text", "").strip().lower() if isinstance(result, dict) else str(result).strip().lower()
                                if self._verbose:
                                    print(f"[wp] stop-check: '{text}'", file=sys.stderr)
                                if any(sw in text for sw in STOP_WORDS):
                                    self._stopped.set()
                                    return
                            except Exception:
                                pass
                            speech_frames.clear()
                            speech_count = 0
                            silent_count = 0


# ── keyboard listener ────────────────────────────────────────────────


class KeyListener:
    """Non-blocking listener for Escape key. Sets a flag when pressed."""

    def __init__(self):
        self.pressed = threading.Event()
        self._running = False
        self._thread: threading.Thread | None = None
        self._old_settings = None

    def start(self):
        self.pressed.clear()
        self._running = True
        try:
            self._old_settings = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
        except termios.error:
            return  # not a tty
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._old_settings is not None:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._old_settings)
            except termios.error:
                pass
            self._old_settings = None
        if self._thread:
            self._thread.join(timeout=0.5)
            self._thread = None

    def _listen(self):
        while self._running and not self.pressed.is_set():
            if select.select([sys.stdin], [], [], 0.05)[0]:
                ch = sys.stdin.read(1)
                if ch == "\x1b":  # Escape
                    self.pressed.set()
                    return


# ── pipelined TTS playback ──────────────────────────────────────────


def speak_response(
    response: str,
    tts_model,
    voice_state,
    whisper,
    vad: webrtcvad.Vad,
    verbose: bool = False,
):
    """Chunk response, synthesize with pipelined TTS, play back. Supports stop words and Escape key."""
    chunks = split_sentences(response)
    if not chunks:
        return

    # Start stop-word listener and key listener
    stop_listener = StopWordListener(whisper, vad, verbose=verbose)
    stop_listener.start()
    key_listener = KeyListener()
    key_listener.start()

    def should_stop():
        return stop_listener.stopped or key_listener.pressed.is_set()

    try:
        # Synthesize first chunk
        wav = tts_model.generate_audio(voice_state, chunks[0], copy_state=True)
        next_wav = None
        next_thread = None

        for i, chunk in enumerate(chunks):
            if should_stop():
                sd.stop()
                if verbose:
                    print("[wp] playback stopped by user", file=sys.stderr)
                break

            if i > 0:
                # Wait for background synthesis
                if next_thread:
                    next_thread.join()
                wav = next_wav

            # Start synthesizing next chunk in background
            if i + 1 < len(chunks):
                container = {}

                def synth(text=chunks[i + 1]):
                    container["wav"] = tts_model.generate_audio(voice_state, text, copy_state=True)

                next_thread = threading.Thread(target=synth)
                next_thread.start()
            else:
                next_thread = None

            if wav is None:
                continue

            # Play current chunk
            audio_data = wav.squeeze()
            if hasattr(audio_data, "numpy"):
                audio_data = audio_data.numpy()
            audio_data = np.asarray(audio_data, dtype=np.float32)

            sd.play(audio_data, samplerate=tts_model.sample_rate)
            # Poll so we can cut playback immediately on stop word or Escape
            while sd.get_stream().active:
                if should_stop():
                    sd.stop()
                    if verbose:
                        print("[wp] playback cut mid-chunk", file=sys.stderr)
                    break
                time.sleep(0.05)

            if should_stop():
                break

            if next_thread:
                next_thread.join()
                next_wav = container.get("wav")
    finally:
        key_listener.stop()
        stop_listener.stop()


# ── main loop ────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        prog="wp listen",
        description="Voice pipeline: mic → VAD → STT → wake word → LLM → TTS",
    )
    parser.add_argument("--wake", action="append", required=True, help="Wake word (repeatable)")
    parser.add_argument("--llm", default="openclaw agent --agent main --message", help="LLM command")
    parser.add_argument("--model", default="distil-small.en", help="Whisper model")
    parser.add_argument("--voice", default="alba", help="TTS voice")
    parser.add_argument("--energy-threshold", type=float, default=ENERGY_THRESHOLD_DBFS, help="dBFS gate")
    parser.add_argument("--no-typing", action="store_true", help="Disable typing sounds")
    parser.add_argument("--verbose", action="store_true", help="Debug output")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    typing_wav = None if args.no_typing else str(script_dir / "Resources" / "typing.wav")

    print("[wp] Loading models...", file=sys.stderr)

    from lightning_whisper_mlx import LightningWhisperMLX
    from pocket_tts import TTSModel

    whisper = LightningWhisperMLX(model=args.model, batch_size=12)
    tts_model = TTSModel.load_model()
    voice_state = tts_model.get_state_for_audio_prompt(args.voice)

    # Warm up whisper with silence
    silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
    whisper.transcribe(silence)

    vad = webrtcvad.Vad(2)

    wake_words = args.wake
    print(f"[wp] Ready — wake words: {wake_words}", file=sys.stderr)
    print("[wp] Listening... (Ctrl-C to stop)", file=sys.stderr)

    busy = False

    def shutdown(sig, frame):
        print("\n[wp] Shutting down...", file=sys.stderr)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while True:
        if busy:
            time.sleep(0.1)
            continue

        pcm = capture_speech(vad, energy_threshold=args.energy_threshold, verbose=args.verbose)
        if pcm is None:
            continue

        audio = pcm.astype(np.float32) / 32768.0
        result = whisper.transcribe(audio)
        text = result.get("text", "").strip() if isinstance(result, dict) else str(result).strip()

        if args.verbose:
            print(f"[wp] heard: '{text}'", file=sys.stderr)

        if not text or not wake_match(text, wake_words):
            continue

        text = strip_wake(text, wake_words)
        if not text:
            continue

        print(f"[wp] Q: {text}", file=sys.stderr)
        busy = True

        # Start typing sounds
        typing_proc = start_typing(typing_wav)

        # Call LLM
        response = run_llm(args.llm, text)

        # Stop typing sounds
        stop_typing(typing_proc)

        if not response:
            print("[wp] (no response)", file=sys.stderr)
            busy = False
            continue

        print(f"[wp] A: {response}", file=sys.stderr)

        # Speak response with pipelined TTS
        speak_response(response, tts_model, voice_state, whisper, vad, verbose=args.verbose)

        busy = False


if __name__ == "__main__":
    main()
