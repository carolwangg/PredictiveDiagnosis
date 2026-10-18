import logging
import os
import subprocess
import tempfile
from pathlib import Path

from flask import Flask, request, jsonify
from faster_whisper import WhisperModel

app = Flask(__name__)

# Maximum upload size: 200 MB
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

# Configuration via environment variables
MODEL_NAME = os.getenv("ASR_MODEL", "large-v3")
API_KEY = os.getenv("API_KEY")
PORT = int(os.getenv("PORT", "8000"))

# Log errors and request processing to the terminal
logging.basicConfig(level=logging.INFO)
app.logger.setLevel(logging.INFO)

# Load Whisper once when the server starts
app.logger.info("Loading Faster-Whisper model: %s", MODEL_NAME)

try:
    model = WhisperModel(
        MODEL_NAME,
        device="cuda",
        compute_type="float16",
    )
    app.logger.info("Whisper loaded successfully on GPU")

except Exception:
    app.logger.exception("GPU initialization failed; falling back to CPU")

    model = WhisperModel(
        MODEL_NAME,
        device="cpu",
        compute_type="int8",
    )
    app.logger.info("Whisper loaded successfully on CPU")


@app.get("/health")
def health():
    return jsonify({
        "status": "ready",
        "model": MODEL_NAME,
    }), 200


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({
        "error": "Audio file exceeds the 200 MB upload limit"
    }), 413


@app.post("/transcribe")
def transcribe():
    # Optional API key authentication
    if API_KEY and request.headers.get("X-API-Key") != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    if "file" not in request.files:
        return jsonify({
            "error": "Missing audio file. Upload it using the 'file' field."
        }), 400

    uploaded = request.files["file"]

    if not uploaded.filename:
        return jsonify({"error": "No filename provided"}), 400

    app.logger.info(
        "Received audio upload: %s",
        Path(uploaded.filename).name,
    )

    try:
        with tempfile.TemporaryDirectory(prefix="asr-") as temp_dir:
            directory = Path(temp_dir)

            # Keep the original extension so FFmpeg can identify the format.
            suffix = Path(uploaded.filename).suffix or ".audio"
            source_file = directory / f"input{suffix}"
            wav_file = directory / "audio.wav"

            uploaded.save(source_file)

            file_size = source_file.stat().st_size

            if file_size == 0:
                return jsonify({"error": "Uploaded audio file is empty"}), 400

            app.logger.info(
                "Saved upload (%s bytes); converting to WAV",
                file_size,
            )

            # Convert audio to mono, 16 kHz PCM WAV for transcription.
            try:
                result = subprocess.run(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel", "error",
                        "-y",
                        "-i", str(source_file),
                        "-vn",
                        "-ac", "1",
                        "-ar", "16000",
                        "-c:a", "pcm_s16le",
                        str(wav_file),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=False,
                )

            except FileNotFoundError:
                app.logger.exception("FFmpeg executable was not found")
                return jsonify({
                    "error": "FFmpeg is not installed or is not on PATH"
                }), 500

            except subprocess.TimeoutExpired:
                app.logger.exception("FFmpeg conversion timed out")
                return jsonify({
                    "error": "Audio conversion timed out"
                }), 408

            if result.returncode != 0:
                app.logger.error("FFmpeg failed: %s", result.stderr)
                return jsonify({
                    "error": "Could not decode the uploaded audio",
                    "details": result.stderr[-2000:],
                }), 400

            if not wav_file.exists() or wav_file.stat().st_size == 0:
                app.logger.error("FFmpeg produced no WAV output")
                return jsonify({
                    "error": "Audio conversion produced an empty file"
                }), 400

            app.logger.info("Starting transcription")

            # Transcribe the converted audio.
            segments, info = model.transcribe(
                str(wav_file),
                language="en",
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=False,
                initial_prompt=(
                    "Paramedic, EMS, ambulance call report, patient "
                    "assessment, vital signs, medications, dosages, "
                    "allergies, dispatch."
                ),
            )

            # Consume the generator inside the try block because inference
            # may not begin until the segments are iterated.
            transcript_parts = []

            for segment in segments:
                text = segment.text.strip()
                if text:
                    transcript_parts.append(text)

            transcript = " ".join(transcript_parts).strip()

            app.logger.info(
                "Transcription completed: language=%s, duration=%.2f seconds",
                info.language,
                info.duration,
            )

            return jsonify({
                "text": transcript,
                "language": info.language,
                "duration_seconds": info.duration,
            }), 200

    except Exception as exc:
        # Keep the complete traceback in the server terminal.
        app.logger.exception("Transcription failed")

        # Include a useful error in the response while debugging.
        # Remove "details" from public-facing responses in production.
        return jsonify({
            "error": "Transcription failed",
            "type": type(exc).__name__,
            "details": str(exc)[:2000],
        }), 500


if __name__ == "__main__":
    app.logger.info("Starting ASR API on 127.0.0.1:%s", PORT)

    app.run(
        host="127.0.0.1",
        port=PORT,
        debug=False,
    )
