from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

# Keep downloaded model weights beside the app while still honoring the user's
# normal Hugging Face login/token location.
os.environ.setdefault(
    "HF_HUB_CACHE", str(Path(__file__).resolve().parent / "models" / "huggingface")
)
os.environ.setdefault(
    "HF_XET_CACHE", str(Path(__file__).resolve().parent / "models" / "xet")
)
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import imageio_ffmpeg
import numpy as np
import scipy.io.wavfile
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pocket_tts import TTSModel, export_model_state


ROOT = Path(__file__).resolve().parent
UPLOADS_DIR = ROOT / "uploads"
VOICES_DIR = ROOT / "voices"
OUTPUTS_DIR = ROOT / "outputs"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_TEXT_CHARS = 12_000
KEEP_OUTPUTS = 30
ALLOWED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}
LANGUAGES = {
    "english": "English",
    "french": "French",
    "german": "German",
    "portuguese": "Portuguese",
    "italian": "Italian",
    "spanish": "Spanish",
}
REFERENCE_MODES = {
    "natural": "Natural · light cleanup",
    "raw": "Already clean · resample only",
    "denoise": "Noisy room · gentle denoise",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("pocket-voice")


def _prepare_directories() -> None:
    for directory in (UPLOADS_DIR, VOICES_DIR, OUTPUTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def _safe_stem(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-_")
    return value[:48] or "my-voice"


def _voice_metadata_path(voice_id: str) -> Path:
    return VOICES_DIR / f"{voice_id}.json"


def _voice_state_path(voice_id: str) -> Path:
    return VOICES_DIR / f"{voice_id}.safetensors"


def _read_metadata(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _list_voices() -> list[dict]:
    voices: list[dict] = []
    for path in VOICES_DIR.glob("*.json"):
        item = _read_metadata(path)
        voice_id = item.get("id")
        if voice_id and _voice_state_path(voice_id).is_file():
            voices.append(item)
    return sorted(voices, key=lambda item: item.get("created_at", 0), reverse=True)


def _ffmpeg_executable() -> str:
    system_ffmpeg = shutil.which("ffmpeg")
    return system_ffmpeg or imageio_ffmpeg.get_ffmpeg_exe()


def _convert_reference(source: Path, destination: Path, cleanup_mode: str = "natural") -> None:
    filters = {
        # Preserve every natural pause. Removing internal silence makes slow or
        # isolated-word recordings sound even more robotic when cloned.
        "natural": "highpass=f=45,lowpass=f=11500,loudnorm=I=-20:TP=-2:LRA=14",
        # For audio that has already been denoised/mastered. Avoid processing it twice.
        "raw": "anull",
        # Conservative denoising for a genuinely noisy room recording.
        "denoise": (
            "highpass=f=65,lowpass=f=11000,afftdn=nf=-35:nr=10:tn=1,"
            "loudnorm=I=-20:TP=-2:LRA=14"
        ),
    }
    if cleanup_mode not in filters:
        raise ValueError("Unknown reference cleanup mode.")
    command = [
        _ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-t",
        "30",
        "-af",
        filters[cleanup_mode],
        "-ar",
        "24000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode != 0 or not destination.is_file():
        detail = result.stderr.strip()[-800:] or "FFmpeg could not decode this file."
        raise ValueError(detail)


@dataclass(frozen=True)
class ModelSettings:
    language: str
    temperature: float
    decode_steps: int
    quantize: bool


class PocketEngine:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._model: TTSModel | None = None
        self._settings: ModelSettings | None = None
        self._voice_cache: dict[tuple[str, str], tuple[float, dict]] = {}

    @property
    def status(self) -> dict:
        with self._lock:
            return {
                "loaded": self._model is not None,
                "device": str(self._model.device) if self._model is not None else "cpu",
                "voice_cloning": (
                    bool(getattr(self._model, "has_voice_cloning", False))
                    if self._model is not None
                    else None
                ),
                "settings": self._settings.__dict__ if self._settings else None,
            }

    def _load_model(
        self, settings: ModelSettings, require_voice_cloning: bool = False
    ) -> TTSModel:
        needs_clone_retry = (
            require_voice_cloning
            and self._model is not None
            and not getattr(self._model, "has_voice_cloning", False)
        )
        if self._model is None or self._settings != settings or needs_clone_retry:
            log.info("Loading Pocket TTS model: %s", settings)
            self._voice_cache.clear()
            self._model = TTSModel.load_model(
                language=settings.language,
                temp=settings.temperature,
                lsd_decode_steps=settings.decode_steps,
                quantize=settings.quantize,
            )
            self._settings = settings
            log.info("Model ready on %s at %s Hz", self._model.device, self._model.sample_rate)
        return self._model

    def _voice_state(self, model: TTSModel, voice_id: str, language: str) -> dict:
        path = _voice_state_path(voice_id)
        if not path.is_file():
            raise FileNotFoundError("That saved voice no longer exists.")
        cache_key = (language, voice_id)
        modified = path.stat().st_mtime
        cached = self._voice_cache.get(cache_key)
        if cached and cached[0] == modified:
            return cached[1]
        state = model.get_state_for_audio_prompt(str(path))
        self._voice_cache[cache_key] = (modified, state)
        return state

    def create_voice(self, wav_path: Path, voice_id: str, settings: ModelSettings) -> None:
        with self._lock:
            model = self._load_model(settings, require_voice_cloning=True)
            if not getattr(model, "has_voice_cloning", False):
                raise PermissionError(
                    "Voice cloning needs access to kyutai/pocket-tts on Hugging Face. "
                    "Accept the model conditions, then run enable-voice-cloning.bat to log in."
                )
            state = model.get_state_for_audio_prompt(str(wav_path), truncate=True)
            export_model_state(state, _voice_state_path(voice_id))
            self._voice_cache[(settings.language, voice_id)] = (
                _voice_state_path(voice_id).stat().st_mtime,
                state,
            )

    def generate(
        self,
        voice_id: str,
        text: str,
        settings: ModelSettings,
        output_path: Path,
        frames_after_eos: int | None,
        speed: float,
    ) -> float:
        with self._lock:
            model = self._load_model(settings)
            voice_state = self._voice_state(model, voice_id, settings.language)
            started = time.perf_counter()
            audio = model.generate_audio(
                voice_state,
                text,
                frames_after_eos=frames_after_eos,
                copy_state=True,
            )
            elapsed = time.perf_counter() - started
            samples = audio.detach().cpu().numpy()
            samples = np.clip(samples, -1.0, 1.0)
            pcm16 = (samples * 32767.0).astype(np.int16)
            if abs(speed - 1.0) < 0.001:
                scipy.io.wavfile.write(output_path, model.sample_rate, pcm16)
            else:
                raw_path = output_path.with_name(f"{output_path.stem}.raw.wav")
                try:
                    scipy.io.wavfile.write(raw_path, model.sample_rate, pcm16)
                    command = [
                        _ffmpeg_executable(),
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(raw_path),
                        "-af",
                        f"atempo={speed:.3f}",
                        "-ar",
                        str(model.sample_rate),
                        "-ac",
                        "1",
                        "-c:a",
                        "pcm_s16le",
                        str(output_path),
                    ]
                    result = subprocess.run(
                        command, capture_output=True, text=True, timeout=120, check=False
                    )
                    if result.returncode != 0 or not output_path.is_file():
                        raise RuntimeError(
                            result.stderr.strip()[-800:] or "Could not adjust speaking speed."
                        )
                finally:
                    raw_path.unlink(missing_ok=True)
            duration = len(samples) / model.sample_rate / speed
            log.info("Generated %.2fs of audio in %.2fs", duration, elapsed)
            return duration


engine = PocketEngine()


def _settings(language: str, temperature: float, decode_steps: int, quantize: bool) -> ModelSettings:
    if language not in LANGUAGES:
        raise HTTPException(400, "Unsupported language.")
    if not 0.1 <= temperature <= 1.5:
        raise HTTPException(400, "Temperature must be between 0.1 and 1.5.")
    if decode_steps not in {1, 2, 3, 4, 5}:
        raise HTTPException(400, "Decode steps must be between 1 and 5.")
    return ModelSettings(language, temperature, decode_steps, quantize)


def _cleanup_outputs() -> None:
    outputs = sorted(OUTPUTS_DIR.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in outputs[KEEP_OUTPUTS:]:
        path.unlink(missing_ok=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _prepare_directories()
    log.info("Pocket Voice ready. FFmpeg: %s", _ffmpeg_executable())
    yield


app = FastAPI(title="Pocket Voice", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "templates" / "index.html")


@app.get("/api/status")
def status() -> dict:
    return {
        "ok": True,
        "engine": engine.status,
        "ffmpeg": Path(_ffmpeg_executable()).name,
        "languages": LANGUAGES,
    }


@app.get("/api/voices")
def voices() -> dict:
    return {"voices": _list_voices()}


@app.post("/api/voices")
async def create_voice(
    file: Annotated[UploadFile, File()],
    name: Annotated[str, Form()],
    language: Annotated[str, Form()] = "english",
    cleanup_mode: Annotated[str, Form()] = "natural",
    consent: Annotated[bool, Form()] = False,
) -> dict:
    if not consent:
        raise HTTPException(400, "Confirm that you have permission to clone this voice.")
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, "Upload an MP3, WAV, M4A, FLAC, or OGG file.")
    if language not in LANGUAGES:
        raise HTTPException(400, "Unsupported language.")
    if cleanup_mode not in REFERENCE_MODES:
        raise HTTPException(400, "Unsupported reference cleanup mode.")

    base = _safe_stem(name)
    voice_id = f"{base}-{uuid.uuid4().hex[:8]}"
    upload_path = UPLOADS_DIR / f"{voice_id}{extension}"
    # Always keep FFmpeg's destination distinct from its source. This matters
    # when the user uploads a WAV, where both paths previously resolved to the
    # same filename and FFmpeg exited with "same as Input #0".
    wav_path = UPLOADS_DIR / f"{voice_id}.normalized.wav"
    total = 0
    try:
        with upload_path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Audio file is larger than 100 MB.")
                destination.write(chunk)
        await asyncio.to_thread(_convert_reference, upload_path, wav_path, cleanup_mode)
        settings = ModelSettings(language, 0.3 if language == "english" else 0.7, 1, False)
        await asyncio.to_thread(engine.create_voice, wav_path, voice_id, settings)
        metadata = {
            "id": voice_id,
            "name": name.strip()[:80] or "My voice",
            "language": language,
            "language_label": LANGUAGES[language],
            "created_at": int(time.time()),
            "source_filename": Path(file.filename or "audio").name[:160],
            "cleanup_mode": cleanup_mode,
            "cleanup_label": REFERENCE_MODES[cleanup_mode],
            "model_label": "English 2026-04" if language == "english" else LANGUAGES[language],
        }
        _voice_metadata_path(voice_id).write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return {"voice": metadata}
    except HTTPException:
        _voice_state_path(voice_id).unlink(missing_ok=True)
        raise
    except PermissionError as exc:
        _voice_state_path(voice_id).unlink(missing_ok=True)
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, subprocess.SubprocessError) as exc:
        _voice_state_path(voice_id).unlink(missing_ok=True)
        raise HTTPException(400, f"Could not process the audio: {exc}") from exc
    except Exception as exc:
        log.exception("Voice extraction failed")
        _voice_state_path(voice_id).unlink(missing_ok=True)
        raise HTTPException(500, f"Voice extraction failed: {exc}") from exc
    finally:
        await file.close()
        upload_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)


@app.delete("/api/voices/{voice_id}")
def delete_voice(voice_id: str) -> dict:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", voice_id):
        raise HTTPException(400, "Invalid voice ID.")
    state_path = _voice_state_path(voice_id)
    metadata_path = _voice_metadata_path(voice_id)
    if not state_path.exists() and not metadata_path.exists():
        raise HTTPException(404, "Voice not found.")
    state_path.unlink(missing_ok=True)
    metadata_path.unlink(missing_ok=True)
    return {"deleted": True}


@app.post("/api/generate")
async def generate(
    voice_id: Annotated[str, Form()],
    text: Annotated[str, Form()],
    language: Annotated[str, Form()] = "english",
    temperature: Annotated[float, Form()] = 0.3,
    decode_steps: Annotated[int, Form()] = 1,
    quantize: Annotated[bool, Form()] = False,
    frames_after_eos: Annotated[int | None, Form()] = None,
    speed: Annotated[float, Form()] = 1.0,
) -> dict:
    text = text.strip()
    if not text:
        raise HTTPException(400, "Enter some text to generate.")
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(400, f"Text is limited to {MAX_TEXT_CHARS:,} characters.")
    metadata = _read_metadata(_voice_metadata_path(voice_id))
    if not metadata:
        raise HTTPException(404, "Choose a saved voice.")
    if metadata.get("language") != language:
        raise HTTPException(400, "This voice was extracted for a different language model.")
    if frames_after_eos is not None and not 0 <= frames_after_eos <= 10:
        raise HTTPException(400, "Tail frames must be between 0 and 10.")
    if not 0.8 <= speed <= 1.2:
        raise HTTPException(400, "Speaking speed must be between 0.8× and 1.2×.")

    model_settings = _settings(language, temperature, decode_steps, quantize)
    output_id = uuid.uuid4().hex
    output_path = OUTPUTS_DIR / f"{output_id}.wav"
    try:
        duration = await asyncio.to_thread(
            engine.generate,
            voice_id,
            text,
            model_settings,
            output_path,
            frames_after_eos,
            speed,
        )
        _cleanup_outputs()
        return {
            "id": output_id,
            "audio_url": f"/api/outputs/{output_id}",
            "download_url": f"/api/outputs/{output_id}?download=1",
            "duration": round(duration, 2),
        }
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        log.exception("Generation failed")
        raise HTTPException(500, f"Generation failed: {exc}") from exc


@app.get("/api/outputs/{output_id}")
def output(output_id: str, download: bool = False) -> FileResponse:
    if not re.fullmatch(r"[a-f0-9]{32}", output_id):
        raise HTTPException(400, "Invalid output ID.")
    path = OUTPUTS_DIR / f"{output_id}.wav"
    if not path.is_file():
        raise HTTPException(404, "Audio output not found.")
    headers = {"Cache-Control": "private, max-age=3600"}
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"pocket-voice-{output_id[:8]}.wav" if download else None,
        headers=headers,
    )
