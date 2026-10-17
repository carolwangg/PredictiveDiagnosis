"""Flask backend for the editable Ambulance Call Report form.

Run:   pip install flask && python app.py        ->  http://127.0.0.1:5000/

Ways to fill the form from the server
  1. Server-side prefill: render_template(..., prefill={...}) (see /report/<id> and /demo).
  2. From the browser:    ACR.load('/api/reports/<id>')  or  ACR.populate({...}).
  3. One field at a time: ACR.set('last_name', 'Doe').
"""
import json
import re
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request

from reference_codes import APGAR, GCS, PAIN_LABELS, PEDIATRIC_VITALS, REFERENCE

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
DATA_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.jinja_env.trim_blocks = True
app.jinja_env.lstrip_blocks = True

ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
DEFAULT_ROWS = 12


def render_form(prefill=None, inline=False):
    ctx = dict(
        prefill=prefill or {},
        proc_rows=max(1, min(request.args.get("rows", DEFAULT_ROWS, type=int), 60)),
        reference=REFERENCE,
        gcs=GCS,
        apgar=APGAR,
        pediatric_vitals=PEDIATRIC_VITALS,
        pain_labels=PAIN_LABELS,
        inline=inline,
    )
    if inline:
        ctx["css"] = (BASE / "static" / "acr.css").read_text(encoding="utf-8")
        ctx["js"] = (BASE / "static" / "acr.js").read_text(encoding="utf-8")
    return render_template("acr.html", **ctx)


def report_path(rid):
    if not ID_RE.fullmatch(rid):
        abort(400, "invalid report id")
    return DATA_DIR / f"{rid}.json"


def read_report(rid):
    path = report_path(rid)
    if not path.exists():
        abort(404)
    return json.loads(path.read_text(encoding="utf-8"))


def clean_payload(payload):
    """Accept a flat {field: value} dict; only str/bool/number/None values (plus 'procedures')."""
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


# --------------------------------------------------------------------- pages
@app.get("/")
def blank_form():
    return render_form()


@app.get("/report/<rid>")
def existing_report(rid):
    return render_form(prefill=read_report(rid))


@app.get("/demo")
def demo():
    """Example of the server populating the form automatically (fictional data)."""
    sample = {
        "service_name": "Example EMS", "service_no": "123", "cacc_call_no": "2026-000123",
        "call_date": "2026-10-17",
        "last_name": "Doe", "first_name": "Jane", "age": "47", "sex": "F", "weight_kg": "68",
        "dob": "1979-03-02",
        "mail_street_no": "10", "mail_street_name": "Sample St", "mail_city": "Toronto",
        "mail_province": "ON", "mail_postal_code": "A1A 1A1", "mail_country": "Canada",
        "pickup_same_as_mailing": True, "pickup_code": "R",
        "occurrence_date": "2026-10-17", "occurrence_time": "14:35",
        "chief_complaint": "Chest pain", "history_cardiac": True, "history_hypertension": True,
        "allergies_nka": True, "trachea_shifted": "midline", "jvd": "not_elevated",
        "ev_call_received": "14:37:10", "ev_arrive_scene": "14:46:02",
        "procedures": [
            {"time": "14:48", "procedure": "010", "crew": "1", "pulse_rate": "92",
             "resp_rate": "18", "bp": "148/90", "spo2": "97"},
            {"time": "14:55", "procedure": "615", "crew": "1", "dose": "0.4 mg", "route": "SL"},
        ],
    }
    return render_form(prefill=sample)


# ----------------------------------------------------------------------- API
@app.post("/api/reports")
def create_report():
    import uuid
    rid = uuid.uuid4().hex[:12]
    report_path(rid).write_text(json.dumps(clean_payload(request.get_json(force=True))), encoding="utf-8")
    return jsonify(id=rid), 201


@app.get("/api/reports/<rid>")
def get_report(rid):
    return jsonify(read_report(rid))


@app.put("/api/reports/<rid>")
def put_report(rid):
    report_path(rid).write_text(json.dumps(clean_payload(request.get_json(force=True))), encoding="utf-8")
    return jsonify(id=rid, saved=True)


if __name__ == "__main__":
    app.run(debug=True)
