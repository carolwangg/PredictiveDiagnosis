"""ems_encounters.json store + mapping between the ACR form and prehospital_scribe.

File layout (ems_encounters.json)
---------------------------------
{"encounters": [
    {
      "encounter_id": "...",            # \
      "chief_complaint": "...",         #  |
      "onset": "...",                   #  |  exactly the fields of
      "allergies": [...],               #  |  prehospital_scribe.PatientRecord
      "medications_administered": [...],#  |  (kept in sync with the form on every save)
      "vitals_stream": [...],           #  |
      "protocol_triggers": [...],       #  |
      "missing_fields": [...],          #  |
      "paramedic_signed_off": false,    # /
      "incident_no": 441370,            # list-page number, auto-assigned
      "created_at": "...", "updated_at": "...",
      "acr": {"last_name": "Doe", ..., "procedures": [{...}]}   # the flat form fields
    }, ...
]}

The ``acr`` block is what the form saves/loads.  Whenever it is saved, the PatientRecord
fields are re-derived from it and ``run_protocol_and_validation_engine`` is re-run, so the
scribe's alerts (missing allergies, active protocols, ...) are always current.  A record
that was created by the scribe pipeline and has no ``acr`` block yet is shown in the form by
mapping its PatientRecord fields back onto form fields.
"""
import json
import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from prehospital_scribe import (
    MedicationAdministered,
    PatientRecord,
    VitalSign,
    run_protocol_and_validation_engine,
)
from reference_codes import REFERENCE

FIRST_INCIDENT_NO = 441367
RECORD_FIELDS = tuple(PatientRecord.model_fields)

# --------------------------------------------------------------- code tables
MED_NAMES = {}
for _title, _lines in REFERENCE:
    if _title == "Medications":
        for _ln in _lines:
            _code, _, _label = _ln.partition("|")
            if _code.isdigit():
                MED_NAMES[_code] = _label
MED_CODES = {name.lower(): code for code, name in MED_NAMES.items()}
MED_CODES.update({"aspirin": "504", "nitro": "615", "gtn": "615", "ventolin": "650",
                  "d50": "530", "dextrose": "530"})
PROC_VITALS, PROC_GLUCOSE, PROC_OTHER_DRUG = "010", "025", "700"

# ------------------------------------------------------------------- helpers
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?$")


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _hm(value):
    """'14:55:00' / '1455' -> '14:55'; anything else is returned stripped."""
    text = str(value or "").strip()
    m = re.match(r"(\d{1,2}):?(\d{2})", text)
    return f"{int(m[1]):02d}:{m[2]}" if m else text


def _int(value):
    m = re.search(r"\d+", str(value or ""))
    return int(m.group()) if m else None


def _text(value):
    return str(value).strip() if value not in (None, False) else ""


# ------------------------------------------------- form  ->  PatientRecord
def form_to_record(acr, prev=None):
    """Derive the scribe's PatientRecord from the flat form dict and run the rule engine."""
    rec = prev.model_copy(deep=True) if prev else PatientRecord()

    rec.chief_complaint = _text(acr.get("chief_complaint")) or None

    date = acr.get("occurrence_date") if _DATE_RE.match(str(acr.get("occurrence_date") or "")) else ""
    stamp = " ".join(p for p in (date, _hm(acr.get("occurrence_time"))) if p)
    if stamp:
        rec.onset = stamp
    elif rec.onset and _STAMP_RE.match(rec.onset):      # stamp we wrote earlier, now cleared
        rec.onset = None

    allergies = []
    if acr.get("allergies_nka"):
        allergies.append("NKA")
    if acr.get("allergies_cno"):
        allergies.append("CNO")
    if acr.get("allergies_other") or _text(acr.get("allergies_details")):
        listed = [a.strip() for a in re.split(r"[,;\n]+", _text(acr.get("allergies_details"))) if a.strip()]
        allergies += listed or ["Other (see details)"]
    rec.allergies = allergies

    meds, vitals = [], []
    for row in acr.get("procedures") or []:
        code, when = _text(row.get("procedure")), _hm(row.get("time"))
        if code in MED_NAMES:
            meds.append(MedicationAdministered(
                drug=(_text(row.get("reading")) if code == PROC_OTHER_DRUG and row.get("reading")
                      else MED_NAMES[code]),
                dose=_text(row.get("dose")),
                route=_text(row.get("route")), timestamp=when))
        glucose = _text(row.get("reading")) if code == PROC_GLUCOSE else ""
        hr, spo2, gcs, bp = (_int(row.get("pulse_rate")), _int(row.get("spo2")),
                             _int(row.get("gcs")), _text(row.get("bp")) or None)
        if any(v is not None for v in (hr, spo2, gcs, bp)) or glucose:
            vitals.append(VitalSign(timestamp=when, bp=bp, hr=hr, spo2=spo2, gcs=gcs,
                                    blood_glucose=glucose or None))
    rec.medications_administered, rec.vitals_stream = meds, vitals

    return run_protocol_and_validation_engine(rec)


# ------------------------------------------------- PatientRecord  ->  form
def record_to_form(rec):
    """Form fields implied by a PatientRecord (used when a record has no saved form yet)."""
    form = {}
    if rec.chief_complaint:
        form["chief_complaint"] = rec.chief_complaint
    if rec.onset:
        form["incident_history"] = f"Onset: {rec.onset}"
    if rec.allergies == ["NKA"]:
        form["allergies_nka"] = True
    elif rec.allergies == ["CNO"]:
        form["allergies_cno"] = True
    elif rec.allergies:
        form["allergies_other"] = True
        form["allergies_details"] = ", ".join(a for a in rec.allergies if a not in ("NKA", "CNO"))

    rows = []
    for v in rec.vitals_stream:
        if any(x is not None for x in (v.bp, v.hr, v.spo2, v.gcs)):
            rows.append({"time": v.timestamp, "procedure": PROC_VITALS,
                         **{k: str(x) for k, x in (("pulse_rate", v.hr), ("bp", v.bp),
                                                   ("spo2", v.spo2), ("gcs", v.gcs)) if x is not None}})
        if v.blood_glucose:
            rows.append({"time": v.timestamp, "procedure": PROC_GLUCOSE, "reading": v.blood_glucose})
    for m in rec.medications_administered:
        code = MED_CODES.get(m.drug.lower())
        row = {"time": m.timestamp, "procedure": code or PROC_OTHER_DRUG,
               "dose": m.dose, "route": m.route}
        if not code:
            row["reading"] = m.drug
        rows.append(row)
    rows.sort(key=lambda r: r["time"])
    if rows:
        form["procedures"] = rows
    return form


# ---------------------------------------------------------- encounter views
def _record_of(enc):
    return PatientRecord(**{k: enc[k] for k in RECORD_FIELDS if k in enc})


def merged_form(enc):
    """What the form page shows: record-derived fields, overridden by the saved form."""
    return {**record_to_form(_record_of(enc)), **(enc.get("acr") or {})}


def summarize(enc):
    """Compact row for the home page list."""
    form = merged_form(enc)
    street = " ".join(p for p in (_text(form.get("mail_street_no")), _text(form.get("mail_street_name"))) if p)
    region = " ".join(p for p in (_text(form.get("mail_province")), _text(form.get("mail_postal_code"))) if p)
    city = ", ".join(p for p in (_text(form.get("mail_city")), region) if p)
    address = [p for p in (street, city) if p] or ([_text(form.get("pickup_location"))]
                                                   if _text(form.get("pickup_location")) else [])
    name = " ".join(p for p in (_text(form.get("first_name")), _text(form.get("last_name"))) if p)

    d = str(form.get("occurrence_date") or "")
    if _DATE_RE.match(d):
        when = f"{d}T{_hm(form.get('occurrence_time')) or '00:00'}"
        when = when if re.search(r"T\d{2}:\d{2}$", when) else f"{d}T00:00"
    else:
        when = enc.get("created_at", "")[:16]
    return {
        "encounter_id": enc["encounter_id"],
        "incident_no": enc["incident_no"],
        "signed": bool(enc.get("paramedic_signed_off")),
        "address": address,
        "name": name,
        "age": _text(form.get("age")),
        "sex": _text(form.get("sex")),
        "chief_complaint": enc.get("chief_complaint") or "",
        "when": when,
        "missing_fields": enc.get("missing_fields", []),
        "protocols": [t.get("protocol", "") for t in enc.get("protocol_triggers", [])],
        "updated_at": enc.get("updated_at", ""),
    }


def detail(enc):
    """Small status block returned after create/update/sign."""
    return {
        "id": enc["encounter_id"],
        "incident_no": enc["incident_no"],
        "signed": bool(enc.get("paramedic_signed_off")),
        "missing_fields": enc.get("missing_fields", []),
        "protocol_triggers": enc.get("protocol_triggers", []),
        "updated_at": enc.get("updated_at", ""),
    }


# ------------------------------------------------------------------- store
class EncounterStore:
    """Thread-safe JSON-file store; writes are atomic (temp file + replace)."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        if not self.path.exists():
            self._write(_seed_encounters())

    # -- file io
    def _read(self):
        data = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        encs = data.get("encounters", []) if isinstance(data, dict) else data   # accept bare list too
        used = {e.get("incident_no") for e in encs if isinstance(e.get("incident_no"), int)}
        nxt = max(used | {FIRST_INCIDENT_NO - 1}) + 1
        for e in encs:                                   # tolerate hand-edited / scribe-written rows
            e.setdefault("encounter_id", PatientRecord().encounter_id)
            if not isinstance(e.get("incident_no"), int):
                e["incident_no"], nxt = nxt, nxt + 1
            e.setdefault("created_at", now_iso())
            e.setdefault("updated_at", e["created_at"])
        return encs

    def _write(self, encs):
        payload = json.dumps({"encounters": encs}, indent=2, ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".ems_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @staticmethod
    def _find(encs, eid):
        return next((e for e in encs if e["encounter_id"] == eid), None)

    # -- queries
    def list(self):
        with self._lock:
            return sorted(self._read(), key=lambda e: e["incident_no"], reverse=True)

    def get(self, eid):
        with self._lock:
            return self._find(self._read(), eid)

    # -- commands
    def create(self, acr):
        with self._lock:
            encs = self._read()
            rec = form_to_record(acr)
            ts = now_iso()
            enc = {**rec.model_dump(),
                   "incident_no": max([e["incident_no"] for e in encs] + [FIRST_INCIDENT_NO - 1]) + 1,
                   "created_at": ts, "updated_at": ts, "acr": acr}
            encs.append(enc)
            self._write(encs)
            return enc

    def update(self, eid, acr):
        with self._lock:
            encs = self._read()
            enc = self._find(encs, eid)
            if enc is None:
                return None
            rec = form_to_record(acr, _record_of(enc))
            enc.update(rec.model_dump())
            enc["acr"], enc["updated_at"] = acr, now_iso()
            self._write(encs)
            return enc

    def set_signed(self, eid, signed):
        with self._lock:
            encs = self._read()
            enc = self._find(encs, eid)
            if enc is None:
                return None
            enc["paramedic_signed_off"], enc["updated_at"] = bool(signed), now_iso()
            self._write(encs)
            return enc

    def apply_scribe_update(self, eid, updates):
        """Merge sparse speech fields into a saved ACR without discarding prior form data."""
        with self._lock:
            encs = self._read()
            enc = self._find(encs, eid)
            if enc is None:
                return None

            acr = merged_form(enc)
            procedures = updates.get("procedures", [])
            for key, value in updates.items():
                if key == "procedures" or value in (None, "", False):
                    continue
                if key == "incident_history":
                    prior = _text(acr.get(key))
                    stamp = datetime.now().astimezone().strftime("%H:%M")
                    value = "\n".join(part for part in (prior, f"[{stamp}] {value}") if part)
                acr[key] = value

            if updates.get("allergies_nka"):
                acr["allergies_other"] = False
                acr["allergies_details"] = ""
            elif updates.get("allergies_other") or updates.get("allergies_details"):
                acr["allergies_nka"] = False

            if procedures:
                acr["procedures"] = [*(acr.get("procedures") or []), *procedures]

            rec = form_to_record(acr, _record_of(enc))
            enc.update(rec.model_dump())
            enc["acr"], enc["updated_at"] = acr, now_iso()
            self._write(encs)
            return enc

    def delete(self, eid):
        with self._lock:
            encs = self._read()
            kept = [e for e in encs if e["encounter_id"] != eid]
            if len(kept) == len(encs):
                return False
            self._write(kept)
            return True


# -------------------------------------------------------------------- seed
def _seed_encounters():
    """First-run sample data (fictional), with deterministic offline scribe data."""
    scribe_rec = run_protocol_and_validation_engine(PatientRecord(
        chief_complaint="Acute chest pain",
        onset="2026-10-17 14:15",
        allergies=["NKA"],
        medications_administered=[MedicationAdministered(
            drug="Aspirin", dose="160 mg", route="Oral", timestamp="14:42")],
        vitals_stream=[VitalSign(timestamp="14:40", bp="140/90", hr=110, spo2=94, gcs=15)],
    ))
    ts = now_iso()

    def from_form(n, acr, signed=False, day="2026-10-17"):
        rec = form_to_record(acr)
        rec.paramedic_signed_off = signed
        return {**rec.model_dump(), "incident_no": n, "created_at": f"{day}T{acr['occurrence_time']}:00",
                "updated_at": ts, "acr": acr}

    base = {"service_name": "Example EMS", "service_no": "123", "mail_province": "ON", "mail_country": "Canada"}
    seeds = [
        {   # 441367 – hypoglycaemia, complete & posted
            "n": 441367, "signed": True, "day": "2026-10-16", "acr": {**base,
            "call_date": "2026-10-16", "occurrence_date": "2026-10-16", "occurrence_time": "09:58",
            "last_name": "Rivera", "first_name": "Sam", "age": "52", "sex": "M",
            "mail_street_no": "974", "mail_street_name": "Lerin Parkway", "mail_city": "Toronto",
            "mail_postal_code": "M0A 0A4", "pickup_code": "R", "chief_complaint": "Altered LOC, diabetic",
            "history_diabetes": True, "allergies_nka": True, "primary_problem": "Diabetic Emergency",
            "problem_code": "83",
            "procedures": [
                {"time": "10:04", "procedure": "010", "crew": "1", "pulse_rate": "96", "resp_rate": "18",
                 "bp": "126/78", "spo2": "98", "gcs": "13"},
                {"time": "10:05", "procedure": "025", "crew": "1", "reading": "2.1 mmol/L"},
                {"time": "10:09", "procedure": "530", "crew": "1", "dose": "25 g", "route": "IV"},
                {"time": "10:22", "procedure": "025", "crew": "1", "reading": "6.4 mmol/L"},
                {"time": "10:23", "procedure": "010", "crew": "1", "pulse_rate": "84", "bp": "130/80",
                 "spo2": "99", "gcs": "15"},
                {"time": "10:24", "procedure": "025", "crew": "1", "reading": "6.5 mmol/L"}]}},
        {   # 441368 – fall, no allergy info yet
            "n": 441368, "signed": False, "day": "2026-10-16", "acr": {**base,
            "call_date": "2026-10-16", "occurrence_date": "2026-10-16", "occurrence_time": "11:04",
            "last_name": "Okafor", "first_name": "Lena", "age": "79", "sex": "F",
            "mail_street_no": "4565", "mail_street_name": "Ariel Blvd", "mail_city": "Toronto",
            "mail_postal_code": "M0A 0A3", "pickup_code": "U", "chief_complaint": "Fall, left hip pain",
            "history_hypertension": True, "trauma1_location": "25", "trauma1_type": "38",
            "trauma1_mechanism": "53", "incident_history": "Slipped in bathroom; unable to bear weight.",
            "procedures": [
                {"time": "11:12", "procedure": "010", "crew": "1", "pulse_rate": "88", "resp_rate": "16",
                 "bp": "152/86", "spo2": "96"}]}},
        {   # 441369 – asthma, posted
            "n": 441369, "signed": True, "day": "2026-10-17", "acr": {**base,
            "call_date": "2026-10-17", "occurrence_date": "2026-10-17", "occurrence_time": "12:12",
            "last_name": "Nguyen", "first_name": "Priya", "age": "34", "sex": "F",
            "mail_street_no": "Suite 823, 6474", "mail_street_name": "Frank Highway", "mail_city": "Toronto",
            "mail_postal_code": "M0A 0A2", "pickup_code": "B", "chief_complaint": "Shortness of breath",
            "history_respiratory": True, "allergies_other": True, "allergies_details": "Penicillin",
            "primary_problem": "Dyspnea", "problem_code": "21",
            "procedures": [
                {"time": "12:20", "procedure": "010", "crew": "1", "pulse_rate": "118", "resp_rate": "28",
                 "bp": "134/82", "spo2": "91"},
                {"time": "12:24", "procedure": "650", "crew": "1", "dose": "5 mg", "route": "NB"},
                {"time": "12:38", "procedure": "010", "crew": "1", "pulse_rate": "104", "resp_rate": "20",
                 "bp": "130/80", "spo2": "96"},
                {"time": "12:40", "procedure": "025", "crew": "1", "reading": "6.1 mmol/L"}]}},
    ]
    encs = [from_form(s["n"], s["acr"], s["signed"], s["day"]) for s in seeds]
    # 441370 comes from the scribe output; the form fields below are what a medic adds on top.
    encs.append({**scribe_rec.model_dump(), "incident_no": 441370,
                 "created_at": "2026-10-17T14:35:00", "updated_at": ts,
                 "acr": {**base, "call_date": "2026-10-17", "occurrence_date": "2026-10-17",
                         "occurrence_time": "14:35", "last_name": "Doe", "first_name": "John",
                         "age": "65", "sex": "M", "mail_street_no": "3728", "mail_street_name": "Maple Grove Rd",
                         "mail_city": "Toronto", "mail_postal_code": "M0A 0A1", "pickup_code": "R",
                         "history_cardiac": True, "primary_problem": "Ischemic", "problem_code": "51",
                         **{k: v for k, v in record_to_form(scribe_rec).items() if k != "incident_history"},
                         "incident_history": "Acute chest pain, onset about 20 minutes prior to call."}})
    return encs
