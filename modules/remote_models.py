"""SSH-tunneled access to remote Whisper and Ollama model services."""
from contextlib import contextmanager
from dataclasses import dataclass
import mimetypes
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from typing import Generator

import requests


class RemoteModelError(RuntimeError):
    """Raised when a remote model cannot be reached or returns invalid output."""


@dataclass(frozen=True)
class RemoteModelConfig:
    ssh_host: str
    ssh_port: int
    ssh_identity_file: str | None
    whisper_api_host: str
    ollama_host: str
    request_timeout: int

    @classmethod
    def from_environment(cls) -> "RemoteModelConfig":
        ssh_host = os.getenv("REMOTE_SSH_HOST", "").strip()
        if not ssh_host:
            raise ValueError("REMOTE_SSH_HOST is required when REMOTE=True.")

        try:
            ssh_port = int(os.getenv("REMOTE_SSH_PORT", "22"))
            request_timeout = int(os.getenv("REMOTE_MODEL_TIMEOUT", "600"))
        except ValueError as error:
            raise ValueError("REMOTE_SSH_PORT and REMOTE_MODEL_TIMEOUT must be integers.") from error
        if not 1 <= ssh_port <= 65535 or request_timeout < 1:
            raise ValueError("REMOTE_SSH_PORT must be 1-65535 and REMOTE_MODEL_TIMEOUT must be positive.")

        identity_file = os.getenv("REMOTE_SSH_KEY", "").strip()
        return cls(
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            ssh_identity_file=str(Path(identity_file).expanduser()) if identity_file else None,
            whisper_api_host=os.getenv("REMOTE_WHISPER_HOST", "127.0.0.1:8000").strip(),
            ollama_host=os.getenv("REMOTE_OLLAMA_HOST", "127.0.0.1:11434").strip(),
            request_timeout=request_timeout,
        )


def _ssh_base(config: RemoteModelConfig) -> list[str]:
    ssh = shutil.which("ssh")
    if ssh is None:
        raise RemoteModelError("OpenSSH client was not found; install or enable ssh.exe.")
    command = [
        ssh, "-T", "-p", str(config.ssh_port),
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
    ]
    if config.ssh_identity_file:
        command.extend(["-i", config.ssh_identity_file])
    return command


@contextmanager
def _remote_service_tunnel(
    config: RemoteModelConfig,
    remote_host: str,
    service_name: str,
) -> Generator[str, None, None]:
    """Forward a local ephemeral port to a service on the SSH host."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        local_port = listener.getsockname()[1]

    command = [
        *_ssh_base(config),
        "-N",
        "-o", "ExitOnForwardFailure=yes",
        "-L", f"127.0.0.1:{local_port}:{remote_host}",
        config.ssh_host,
    ]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        raise RemoteModelError(f"Could not start SSH tunnel for remote {service_name}: {error}") from error

    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                _, stderr = process.communicate()
                message = stderr.decode("utf-8", errors="replace").strip()
                raise RemoteModelError(
                    f"Remote {service_name} SSH tunnel failed: "
                    f"{message or 'SSH exited before the tunnel was ready.'}"
                )
            try:
                with socket.create_connection(("127.0.0.1", local_port), timeout=0.25):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RemoteModelError(f"Timed out opening the SSH tunnel for remote {service_name}.")
        yield f"http://127.0.0.1:{local_port}"
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


@contextmanager
def remote_whisper_tunnel(config: RemoteModelConfig) -> Generator[str, None, None]:
    """Forward a local port to the VM's Faster-Whisper API."""
    with _remote_service_tunnel(
        config, config.whisper_api_host, "Whisper"
    ) as local_host:
        yield local_host


@contextmanager
def remote_ollama_tunnel(config: RemoteModelConfig) -> Generator[str, None, None]:
    """Forward a local port to the VM's Ollama endpoint for one extraction request."""
    with _remote_service_tunnel(
        config, config.ollama_host, "Ollama"
    ) as local_host:
        yield local_host


def transcribe_audio_file_remote(audio_file: str | Path, config: RemoteModelConfig) -> str:
    """Transcribe an audio file using the remote Faster-Whisper API over SSH."""
    audio_path = Path(audio_file)
    content_type = mimetypes.guess_type(audio_path.name)[0] or "audio/mp4"
    try:
        with remote_whisper_tunnel(config) as whisper_host:
            with audio_path.open("rb") as audio:
                response = requests.post(
                    f"{whisper_host}/transcribe",
                    files={"file": (audio_path.name, audio, content_type)},
                    timeout=config.request_timeout,
                )
            if not response.ok:
                print("Remote Whisper status:", response.status_code, flush=True)
                print("Remote Whisper server error:", response.text, flush=True)
            response.raise_for_status()
            result = response.json()
    except requests.RequestException as error:
        raise RemoteModelError(f"Remote Whisper request failed: {error}") from error
    except ValueError as error:
        raise RemoteModelError("Remote Whisper returned invalid JSON.") from error

    transcript = result.get("text") if isinstance(result, dict) else None
    if not isinstance(transcript, str):
        raise RemoteModelError("Remote Whisper response must contain a text field.")
    return transcript
