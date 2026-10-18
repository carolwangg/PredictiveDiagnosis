"""Flask backend for the Ambulance Call Report app.

Run:   pip install flask pydantic ollama numpy faster-whisper sounddevice && python app.py
       ollama pull llama3:latest
       ->  http://127.0.0.1:5000/

Pages
  /                     home: every call report in ems_encounters.json (add / open-to-update / remove)
  /encounters/new       blank form (saved to ems_encounters.json on first Save)
  /encounters/<id>      the form, prefilled from that encounter

JSON API  (all data lives in ems_encounters.json, see encounter_store.py)
  GET    /api/encounters                 list summaries (home page)
  POST   /api/encounters                 create from a flat form dict
  GET    /api/encounters/<id>            full encounter + merged form fields
  PUT    /api/encounters/<id>            replace the saved form; re-runs the scribe rule engine
  DELETE /api/encounters/<id>            remove
  POST   /api/encounters/<id>/sign       {"signed": true|false}  -> paramedic_signed_off
  POST   /api/encounters/<id>/audio      multipart audio recording -> transcribed ACR field updates
"""
import os
import re
from pathlib import Path
import tempfile

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from encounter_store import EncounterStore, detail, merged_form, summarize

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
DATA_FILE = BASE / "ems_encounters.json"
DEFAULT_ROWS = 12

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
app.config["TEST_AUDIO_IMPORT"] = os.getenv("TEST", "").strip().lower() in {
    "1", "true", "yes", "on"
}
app.jinja_env.trim_blocks = True
app.jinja_env.lstrip_blocks = True

store = EncounterStore(DATA_FILE)
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


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


# ---------------------------------------------------------------------- API
@app.get("/api/encounters")
def api_list():
    return jsonify(encounters=[summarize(e) for e in store.list()])


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


@app.post("/api/encounters/<eid>/audio")
def api_audio_update(eid):
    """Transcribe an uploaded browser recording and apply extracted ACR fields."""
    eid = valid_id(eid)
    enc = get_or_404(eid)
    if enc.get("paramedic_signed_off"):
        abort(409, "reopen this report before adding a voice update")

    audio = request.files.get("audio")
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

    from prehospital_scribe import extract_data_with_llm
    from modules.stt import transcribe_audio_file

    with tempfile.TemporaryDirectory(prefix="acr_audio_") as temp_dir:
        audio_path = Path(temp_dir) / ("recording" + suffix)
        audio.save(audio_path)
        if audio_path.stat().st_size == 0:
            abort(400, "audio recording is empty")
        transcript = transcribe_audio_file(audio_path)
    print(f"[Whisper transcript] {transcript}", flush=True)

    if not transcript.strip():
        abort(422, "no speech could be transcribed")
    try:
        extracted = extract_data_with_llm(transcript)
    except ValueError as error:
        abort(502, f"scribe returned invalid structured data: {error}")
    print(f"[Ollama formatted ACR fields] {extracted}", flush=True)
    updates = {key: value for key, value in extracted.items()
               if key == "procedures" or value not in (None, "", False, [])}
    if not updates:
        abort(422, "no ACR fields could be extracted from the recording")

    try:
        updated = store.apply_scribe_update(eid, updates)
    except PermissionError as error:
        abort(409, str(error))
    if updated is None:
        abort(404, "encounter not found")
    return jsonify({**detail(updated), "form": merged_form(updated)})


if __name__ == "__main__":
    app.run(debug=True)
