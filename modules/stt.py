# modules/stt.py
import numpy as np
from faster_whisper import WhisperModel
from config import SAMPLE_RATE, BLOCK_SIZE, WHISPER_MODEL_SIZE
from pathlib import Path

# Load faster-whisper model quantized to int8 for speed
stt_model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")


def listen_for_speech() -> np.ndarray:
    """
    Push-to-Talk implementation: Records microphone input only while
    actively recording, controlled by user input (Press Enter to start/stop).
    """
    audio_frames = []
    is_recording = True
    import sounddevice as sd

    def callback(indata, frames, time_info, status):
        if is_recording:
            audio_frames.append(indata.copy())

    print("\n[🎙️] PUSH-TO-TALK READY")
    input("👉 Press [Enter] to START recording your patient report...")

    print("[🔴] RECORDING ACTIVE... Speak clearly into your mic. (Press [Enter] again when finished)")

    # Start the input stream in the background
    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, callback=callback, blocksize=BLOCK_SIZE)
    stream.start()

    # Wait for the user to press Enter again to stop
    input()

    is_recording = False
    stream.stop()
    stream.close()

    print("[🛑] Recording stopped. Transcribing...")

    if not audio_frames:
        return np.array([], dtype=np.float32)

    return np.concatenate(audio_frames, axis=0).flatten().astype(np.float32)


def transcribe_audio(audio_array: np.ndarray) -> str:
    """Transcribes raw float32 audio arrays using faster-whisper."""
    if audio_array.size == 0:
        return ""

    segments, _ = stt_model.transcribe(audio_array, beam_size=1)
    text = " ".join([segment.text.strip() for segment in segments if segment.text.strip()]).strip()
    return text


def transcribe_audio_file(audio_file: str | Path) -> str:
    """Transcribe an uploaded browser recording in a format supported by faster-whisper."""
    segments, _ = stt_model.transcribe(str(audio_file), beam_size=1)
    return " ".join(
        segment.text.strip()
        for segment in segments
        if segment.text.strip()
    ).strip()