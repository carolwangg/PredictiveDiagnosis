"""Flask backend for the Ambulance Call Report app.

Run:   pip install flask pydantic ollama numpy faster-whisper sounddevice && python app.py
       ollama pull llama3:latest
       ->  http://127.0.0.1:5000/

Pages
  /                     home: every call report in ems_encounters.json (add / open-to-update / remove)
  /encounters/new       blank form (saved to ems_encounters.json on first Save)
  /encounters/<id>      the form, prefilled from that encounter
  /encounters/<id>/fhir the encounter as a FHIR R4 JSON Bundle

JSON API  (all data lives in ems_encounters.json, see encounter_store.py)
  GET    /api/encounters                 list summaries (home page)
  POST   /api/encounters                 create from a flat form dict
  GET    /api/encounters/<id>            full encounter + merged form fields
  PUT    /api/encounters/<id>            replace the saved form; re-runs the scribe rule engine
  DELETE /api/encounters/<id>            remove
  POST   /api/encounters/<id>/sign       {"signed": true|false}  -> paramedic_signed_off
  POST   /api/encounters/<id>/audio      audio -> transcribed ACR field updates
  POST   /api/encounters/<id>/text       test-mode transcript -> extracted ACR field updates
"""
import os
import re
from pathlib import Path
import tempfile

from dotenv import load_dotenv
from flask import Flask, Response, abort, jsonify, render_template, request, url_for
from werkzeug.exceptions import HTTPException

from encounter_store import EncounterStore, detail, merged_form, summarize
from modules.remote_models import (
    RemoteModelConfig,
    RemoteModelError,
    remote_ollama_tunnel,
    transcribe_audio_file_remote,
)

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
DATA_FILE = BASE / "ems_encounters.json"
DEFAULT_ROWS = 12

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
app.config["TEST_AUDIO_IMPORT"] = os.getenv("TEST", "").strip().lower() in {
    "1", "true", "yes", "on"
}
app.config["REMOTE_MODELS"] = os.getenv("REMOTE", "").strip().lower() in {
    "1", "true", "yes", "on"
}
app.jinja_env.trim_blocks = True
app.jinja_env.lstrip_blocks = True

store = EncounterStore(DATA_FILE)
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
REMOTE_MODEL_CONFIG: RemoteModelConfig | None = None
if app.config["REMOTE_MODELS"]:
    REMOTE_MODEL_CONFIG = RemoteModelConfig.from_environment()


# ------------------------------------------------------------------ helpers
def render_form(prefill=None, inline=False, encounter=None):
    ctx = dict(
        prefill=prefill or {},
        proc_rows=max(1, min(request.args.get("rows", DEFAULT_ROWS, type=int), 60)),
        inline=inline,
        encounter=encounter,
        test_audio_import=app.config["TEST_AUDIO_IMPORT"],
    )
    if inline:
        ctx["css"] = (BASE / "static" / "acr.css").read_text(encoding="utf-8")
        ctx["js"] = (BASE / "static" / "acr.js").read_text(encoding="utf-8")
    return render_template("acr.html", **ctx)


def encounter_ctx(enc=None):
    """The small dict encounter.js + the toolbar need (None enc = unsaved new report)."""
    return {
        "id": enc["encounter_id"] if enc else None,
        "incident_no": enc["incident_no"] if enc else None,
        "signed": bool(enc and enc.get("paramedic_signed_off")),
        "missing_fields": enc.get("missing_fields", []) if enc else [],
        "protocol_triggers": enc.get("protocol_triggers", []) if enc else [],
        "api": "/api/encounters",
        "home": "/",
        "page": "/encounters",
    }


def valid_id(eid):
    if not ID_RE.fullmatch(eid):
        abort(400, "invalid encounter id")
    return eid


def get_or_404(eid):
    enc = store.get(valid_id(eid))
    if enc is None:
        abort(404, "encounter not found")
    return enc


def _transcribe_audio_upload(audio, use_remote_whisper=False):
    if audio is None or not audio.filename:
        abort(400, "audio recording is required")

    extensions = {
        "audio/webm": ".webm",
        "video/webm": ".webm",
        "audio/ogg": ".ogg",
        "audio/mp4": ".mp4",
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/x-pn-wav": ".wav",
        "audio/x-m4a": ".m4a",
        "audio/m4a": ".m4a",
    }
    suffix = extensions.get(audio.mimetype)
    if suffix is None:
        suffix = {
            ".webm": ".webm",
            ".ogg": ".ogg",
            ".mp4": ".mp4",
            ".m4a": ".m4a",
            ".mp3": ".mp3",
            ".wav": ".wav",
        }.get(Path(audio.filename).suffix.lower())
    if suffix is None:
        abort(415, "unsupported audio format")

    with tempfile.TemporaryDirectory(prefix="acr_audio_") as temp_dir:
        audio_path = Path(temp_dir) / ("recording" + suffix)
        audio.save(audio_path)
        if audio_path.stat().st_size == 0:
            abort(400, "audio recording is empty")
        if use_remote_whisper:
            try:
                return transcribe_audio_file_remote(audio_path, REMOTE_MODEL_CONFIG)
            except RemoteModelError as error:
                abort(502, str(error))

        from modules.stt import transcribe_audio_file

        return transcribe_audio_file(audio_path)


def clean_payload(payload):
    """Flat {field: value} dict; only str/bool/number/None values (plus 'procedures')."""
    if not isinstance(payload, dict):
        abort(400, "expected a JSON object")
    out = {}
    for key, value in payload.items():
        if not isinstance(key, str) or len(key) > 100:
            abort(400, "bad field name")
        if key == "procedures":
            if not isinstance(value, list) or not all(isinstance(r, dict) for r in value):
                abort(400, "procedures must be a list of objects")
            out[key] = value
        elif value is None or isinstance(value, (str, bool, int, float)):
            out[key] = value
        else:
            abort(400, f"unsupported value for {key}")
    return out


@app.errorhandler(HTTPException)
def json_errors(err):
    if request.path.startswith("/api/"):
        return jsonify(error=err.description), err.code
    return err


# -------------------------------------------------------------------- pages
@app.get("/")
def home():
    return render_template("home.html")


@app.get("/encounters/new")
def new_report():
    return render_form(encounter=encounter_ctx())


@app.get("/encounters/<eid>")
def open_report(eid):
    enc = get_or_404(eid)
    return render_form(prefill=merged_form(enc), encounter=encounter_ctx(enc))


@app.get("/encounters/<eid>/fhir")
def encounter_fhir(eid):
    enc = get_or_404(eid)
    from acr_to_fhir import acr_to_fhir

    fhir_id = re.sub(r"[^A-Za-z0-9.-]", "-", enc["encounter_id"])[:64]
    if not fhir_id[0].isalnum():
        fhir_id = ("enc-" + fhir_id)[:64]
    bundle = acr_to_fhir(merged_form(enc), encounter_id=fhir_id)
    response = jsonify(bundle)
    response.mimetype = "application/fhir+json"
    return response


def _normalize_voice_command(transcript):
    return " ".join(transcript.casefold().split()).strip(" \t\r\n.,!?")


def _send_incident_number(transcript):
    command = _normalize_voice_command(transcript)
    match = re.fullmatch(
        r"send incident (?:number )?(\d+(?:,\d{3})*)",
        command,
    )
    return int(match.group(1).replace(",", "")) if match else None


def _voice_command_target(transcript, encounters):
    command = _normalize_voice_command(transcript)
    if command == "new incident":
        print("voice command: new incident")
        return url_for("new_report")

    match = re.fullmatch(r"edit incident (?:number )?(\d+(?:,\d{3})*)", command)
    if not match:
        return None
    incident_number = int(match.group(1).replace(",", ""))
    encounter = next(
        (
            item for item in encounters
            if item.get("incident_no") == incident_number
        ),
        None,
    )
    if encounter is None:
        print("voice command: open incident id not recognized")
        return None
    print(f"voice command: open incident {encounter['encounter_id']}")
    return url_for("open_report", eid=encounter["encounter_id"])


# ---------------------------------------------------------------------- API
@app.get("/api/encounters")
def api_list():
    return jsonify(encounters=[summarize(e) for e in store.list()])


@app.post("/api/voice-command")
def api_voice_command():
    """Transcribe a home-page spoken navigation command without invoking the scribe."""
    transcript = _transcribe_audio_upload(
        request.files.get("audio"),
        use_remote_whisper=REMOTE_MODEL_CONFIG is not None,
    )
    print(f"voice command: {transcript}")
    encounters = store.list()
    incident_number = _send_incident_number(transcript)
    if incident_number is not None:
        encounter = next(
            (
                item for item in encounters
                if item.get("incident_no") == incident_number
            ),
            None,
        )
        if encounter is None:
            return jsonify(
                transcript=transcript,
                posted=False,
                incident_no=incident_number,
            )
        if encounter.get("paramedic_signed_off"):
            return jsonify(
                transcript=transcript,
                posted=False,
                already_posted=True,
                incident_no=incident_number,
            )
        updated = store.set_signed(encounter["encounter_id"], True)
        if updated is None:
            abort(404, "encounter not found")
        return jsonify(
            transcript=transcript,
            posted=True,
            **detail(updated),
        )

    target = _voice_command_target(transcript, encounters)
    return jsonify(transcript=transcript, redirect_url=target)


@app.post("/api/encounters")
def api_create():
    enc = store.create(clean_payload(request.get_json(force=True)))
    return jsonify(detail(enc)), 201


@app.get("/api/encounters/<eid>")
def api_get(eid):
    enc = get_or_404(eid)
    return jsonify({**enc, "form": merged_form(enc)})


@app.put("/api/encounters/<eid>")
def api_update(eid):
    enc = store.update(valid_id(eid), clean_payload(request.get_json(force=True)))
    if enc is None:
        abort(404, "encounter not found")
    return jsonify(detail(enc))


@app.delete("/api/encounters/<eid>")
def api_delete(eid):
    if not store.delete(valid_id(eid)):
        abort(404, "encounter not found")
    return jsonify(deleted=True)


@app.post("/api/encounters/<eid>/sign")
def api_sign(eid):
    body = request.get_json(silent=True) or {}
    enc = store.set_signed(valid_id(eid), body.get("signed", True))
    if enc is None:
        abort(404, "encounter not found")
    return jsonify(detail(enc))


@app.post("/api/encounters/<eid>/transcribe")
def api_transcribe_audio(eid):
    """Transcribe a live recording snapshot without applying fields to the report."""
    eid = valid_id(eid)
    enc = get_or_404(eid)
    if enc.get("paramedic_signed_off"):
        abort(409, "reopen this report before recording an update")

    transcript = _transcribe_audio_upload(request.files.get("audio"))
    print(f"[Live Whisper transcript] {transcript}", flush=True)
    return jsonify(transcript=transcript)


@app.post("/api/encounters/<eid>/audio")
@app.post("/api/encounters/<eid>/text")
def api_audio_update(eid):
    """Process audio or test-mode text directly, then apply extracted ACR fields."""
    eid = valid_id(eid)
    enc = get_or_404(eid)
    if enc.get("paramedic_signed_off"):
        abort(409, "reopen this report before adding a voice update")

    from prehospital_scribe import extract_data_with_llm

    if request.path.endswith("/text") or request.form.get("source") == "text":
        if not app.config["TEST_AUDIO_IMPORT"]:
            abort(403, "typed transcript input is available only in test mode")
        payload = request.get_json(silent=True) if request.is_json else request.form
        if request.is_json and not isinstance(payload, dict):
            abort(400, "transcript text is required")
        transcript_value = payload.get("transcript", "") if payload else ""
        if not isinstance(transcript_value, str) or not transcript_value.strip():
            abort(400, "transcript text is required")
        transcript = transcript_value.strip()
        section = (payload.get("section") or None) if payload else None
        print(f"[Typed transcript] {transcript}", flush=True)
    else:
        section = request.form.get("section") or None
        if section is not None:
            from prehospital_scribe import ACR_SECTION_FIELDS

            if section not in ACR_SECTION_FIELDS:
                abort(400, "unsupported ACR section")
        use_remote_whisper = (
            app.config["TEST_AUDIO_IMPORT"]
            and request.form.get("source") == "file"
            and REMOTE_MODEL_CONFIG is not None
        )
        transcript = _transcribe_audio_upload(
            request.files.get("audio"),
            use_remote_whisper=use_remote_whisper,
        )
        print(f"[Whisper transcript] {transcript}", flush=True)

    if section is not None:
        from prehospital_scribe import ACR_SECTION_FIELDS

        if not isinstance(section, str) or section not in ACR_SECTION_FIELDS:
            abort(400, "unsupported ACR section")
    if not transcript.strip():
        abort(422, "no speech could be transcribed")
    if (
        request.path.endswith("/audio")
        and section is None
        and _normalize_voice_command(transcript) == "send incident"
    ):
        updated = store.set_signed(eid, True)
        if updated is None:
            abort(404, "encounter not found")
        return jsonify(
            **detail(updated),
            command="post-incident",
            transcript=transcript,
        )
    if request.path.endswith("/audio") and app.config["TEST_AUDIO_IMPORT"]:
        response = {"transcript": transcript, "awaiting_review": True}
        if section:
            response["section"] = section
        return jsonify(response)

    from ollama import ResponseError

    try:
        extraction_options = {"section": section} if section else {}
        if REMOTE_MODEL_CONFIG is not None:
            with remote_ollama_tunnel(REMOTE_MODEL_CONFIG) as ollama_host:
                extracted = extract_data_with_llm(
                    transcript,
                    ollama_host=ollama_host,
                    ollama_timeout=REMOTE_MODEL_CONFIG.request_timeout,
                    **extraction_options,
                )
        else:
            extracted = extract_data_with_llm(transcript, **extraction_options)
    except ValueError as error:
        abort(502, f"scribe returned invalid structured data: {error}")
    except RemoteModelError as error:
        abort(502, str(error))
    except (ConnectionError, ResponseError) as error:
        abort(502, f"Ollama request failed: {error}")
    updates = {
        key: value for key, value in extracted.items()
        if (key == "procedures" and value)
        or (key != "procedures" and value not in (None, "", False, []))
    }
    if not updates:
        abort(422, "no ACR fields could be extracted from the recording")

    try:
        updated = store.apply_scribe_update(eid, updates)
    except PermissionError as error:
        abort(409, str(error))
    if updated is None:
        abort(404, "encounter not found")
    return jsonify({
        **detail(updated),
        "form": merged_form(updated),
        "transcript": transcript,
        **({"section": section} if section else {}),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
