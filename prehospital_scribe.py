"""Speech extraction for the Ambulance Call Report form and CLI scribe."""
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Optional
import uuid

from pydantic import BaseModel, Field

from reference_codes import REFERENCE

BASE_DIR = Path(__file__).resolve().parent
MASTER_DB_FILE = BASE_DIR / "ems_encounters.json"
FEEDBACK_LOG_FILE = BASE_DIR / "feedback_log.json"

PROCEDURE_FIELDS = {
    "time", "procedure", "crew", "reading", "result_code", "resp_rate",
    "pulse_rate", "bp", "temp", "spo2", "pupil_r", "pupil_l", "dose",
    "route", "gcs", "pain",
}
CHECKBOX_FIELDS = {
    "allergies_nka", "allergies_cno", "allergies_other",
    "history_previously_healthy", "history_cardiac", "history_respiratory",
    "history_seizure", "history_diabetes", "history_psychiatric",
    "history_anaphylaxis", "history_cancer", "history_stroke_tia",
    "history_hypertension", "history_other", "history_cno",
    "meds_nitrates", "meds_asa", "meds_salbutamol", "meds_furosemide",
    "meds_insulin_oral_diabetic", "meds_pde_inhibitors", "meds_other",
    "meds_none", "meds_cno", "blood_thinners", "skin_colour_flushed",
    "skin_colour_pale", "skin_colour_cyanosis", "skin_colour_jaundice",
    "skin_colour_unremarkable", "skin_condition_dry", "skin_condition_clammy",
    "skin_condition_diaphoretic", "skin_condition_unremarkable",
    "air_entry_bilateral", "air_entry_decreased_r", "air_entry_decreased_l",
    "breath_clear", "breath_wheezes", "breath_crackles", "breath_rub",
    "breath_absent", "abdomen_soft", "abdomen_rigid", "abdomen_distended",
    "abdomen_tender", "abdomen_mass", "abdomen_pulsatile", "abdomen_ru",
    "abdomen_lu", "abdomen_ll", "abdomen_rl", "abdomen_center",
    "back_pelvis_unremarkable", "extremities_unremarkable",
}
ACR_SCALAR_FIELDS = {
    "first_name", "last_name", "age", "sex", "mail_street_no", "mail_street_name",
    "mail_city", "mail_province", "mail_postal_code", "pickup_location",
    "call_date", "ev_call_received", "occurrence_date",
    "occurrence_time", "chief_complaint", "incident_history", "remarks",
    "allergies_details", "history_details", "medications_details",
    "general_appearance", "peripheral_edema", "pedal_pulse", "primary_problem",
    "problem_code",
}
ACR_FIELDS = ACR_SCALAR_FIELDS | CHECKBOX_FIELDS


def _build_extraction_prompt(raw_transcript: str) -> str:
    scalar_fields = ", ".join(sorted(ACR_SCALAR_FIELDS))
    checkbox_fields = ", ".join(sorted(CHECKBOX_FIELDS))
    procedure_fields = ", ".join(sorted(PROCEDURE_FIELDS))
    return f"""
You extract only facts explicitly stated in a paramedic's prehospital report.
Return one object using the exact field names in the supplied JSON schema. Include
every required field. Use an empty string for unavailable text fields, false for
checkboxes that are not explicitly supported, and an empty list when there are no
documented procedures. Never guess, infer a negative clinical finding, or add prose.

Scalar form fields: {scalar_fields}.
Checkbox fields: {checkbox_fields}. Checkbox values must be JSON booleans (true or false).
For peripheral_edema and pedal_pulse, use the exact radio choice "absent" or "present".
The procedures field is a list of rows using only these procedure columns:
{procedure_fields}. Each procedure row must include every listed column, using an
empty string when a value is unavailable. Do not add other fields.

Use ACR date format YYYY-MM-DD and time format HH:MM. Only set a checkbox to true or
false when the transcript explicitly supports that value; otherwise use false. Use the
value "M" or "F" for sex when clear, otherwise the stated description. For an administered
drug, add a procedure row with its medication procedure code when known; use code "700"
for another drug and put its name in reading. Use code "010" for a row of vital signs
and "025" for a blood glucose reading. Preserve units in glucose readings and doses.
For a vitals row, use ACR names (pulse_rate, bp, spo2, gcs, resp_rate). Keep
incident_history concise, chronological, and factual. Do not create empty procedure
rows. If nothing can be safely extracted, return empty strings, false values, and an
empty procedures list as required by the schema.

Transcript:
{json.dumps(raw_transcript)}
"""


def build_acr_schema() -> dict:
    """Build the JSON schema Ollama uses to constrain ACR extraction output."""
    properties = {
        field: {"type": "boolean" if field in CHECKBOX_FIELDS else "string"}
        for field in sorted(ACR_FIELDS)
    }
    properties["procedures"] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                field: {"type": "string"} for field in sorted(PROCEDURE_FIELDS)
            },
            "required": sorted(PROCEDURE_FIELDS),
            "additionalProperties": False,
        },
    }
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(properties),
        "additionalProperties": False,
    }


_ACR_SYSTEM_PROMPT = (
    "You extract documented facts from paramedic transcripts. "
    "Never invent missing information. Use only fields in the supplied schema. "
    "Do not infer examination findings from normal expectations. Use empty strings "
    "for unavailable text fields and false for unchecked boolean fields. "
    "Return only the schema."
)


def _chat_with_acr_schema(chat, model: str, prompt: str):
    return chat(
        model=model,
        messages=[
            {"role": "system", "content": _ACR_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        format=build_acr_schema(),
        options={"temperature": 0, "num_predict": 2048},
    )
MEDICATION_NAMES = {}
for section_title, section_lines in REFERENCE:
    if section_title == "Medications":
        for line in section_lines:
            code, _, label = line.partition("|")
            if code.isdigit():
                MEDICATION_NAMES[code] = label


class VitalSign(BaseModel):
    timestamp: Optional[str] = None
    bp: Optional[str] = None
    hr: Optional[int] = None
    spo2: Optional[int] = None
    gcs: Optional[int] = None
    blood_glucose: Optional[str] = None


class MedicationAdministered(BaseModel):
    drug: str
    dose: str = ""
    route: str = ""
    timestamp: Optional[str] = None


class PatientRecord(BaseModel):
    encounter_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    chief_complaint: Optional[str] = None
    onset: Optional[str] = None
    allergies: list[str] = Field(default_factory=list)
    medications_administered: list[MedicationAdministered] = Field(default_factory=list)
    vitals_stream: list[VitalSign] = Field(default_factory=list)
    protocol_triggers: list[dict] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    paramedic_signed_off: bool = False


def _as_text(value):
    return str(value).strip() if value not in (None, False) else ""


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _validate_extraction(extraction):
    if not isinstance(extraction, dict):
        raise ValueError("Scribe response must be a JSON object.")
    if "call_time" in extraction:
        extraction.setdefault("ev_call_received", extraction["call_time"])
        del extraction["call_time"]
    unknown = set(extraction) - ACR_FIELDS - {"procedures"}
    if unknown:
        raise ValueError("Scribe returned unsupported ACR fields: " + ", ".join(sorted(unknown)))
    for key in list(extraction):
        value = extraction[key]
        if value is None:
            del extraction[key]
        elif key == "procedures":
            if not isinstance(value, list):
                raise ValueError("Scribe field procedures must be a list.")
            normalized_rows = []
            for index, row in enumerate(value):
                if not isinstance(row, dict):
                    raise ValueError(f"Scribe procedure row {index + 1} must be an object.")
                unknown_columns = set(row) - PROCEDURE_FIELDS
                if unknown_columns:
                    raise ValueError(
                        f"Scribe procedure row {index + 1} has unsupported fields: "
                        + ", ".join(sorted(unknown_columns))
                    )
                normalized_row = {}
                for field, column_value in row.items():
                    if column_value is None:
                        normalized_row[field] = ""
                    elif isinstance(column_value, bool) or not isinstance(
                        column_value, (str, int, float)
                    ):
                        raise ValueError(
                            f"Scribe procedure field {field} must be a scalar."
                        )
                    else:
                        normalized_row[field] = str(column_value).strip()
                normalized_rows.append(normalized_row)
            extraction[key] = normalized_rows
        elif key in CHECKBOX_FIELDS:
            if not isinstance(value, bool):
                raise ValueError(f"Scribe field {key} must be a boolean.")
        elif isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError(f"Scribe field {key} must be a scalar.")
        else:
            extraction[key] = str(value).strip()
    return extraction


def extract_data_with_llm(
    raw_transcript: str,
    *,
    ollama_host: str | None = None,
    ollama_timeout: int | None = None,
) -> dict:
    """Return sparse, flat ACR fields and procedure rows from a voice transcript."""
    if not raw_transcript.strip():
        raise ValueError("Cannot extract ACR fields from an empty transcript.")

    prompt = raw_transcript #_build_extraction_prompt(raw_transcript)
    if ollama_host:
        from ollama import Client

        if ollama_timeout is None:
            client = Client(host=ollama_host)
        else:
            client = Client(host=ollama_host, timeout=ollama_timeout)
        response = _chat_with_acr_schema(client.chat, "qwen2.5:7b", prompt)
    else:
        from ollama import chat

        response = _chat_with_acr_schema(chat, "llama3:latest", prompt)
    content = response.message.content
    print(f"[Ollama formatted ACR fields] {content}", flush=True)

    def seconds(ns):
        return round(ns / 1_000_000_000, 3)

    print("Model load:", seconds(response.load_duration))
    print("Input processing:", seconds(response.prompt_eval_duration))
    print("Output generation:", seconds(response.eval_duration))
    print("Input tokens:", response.prompt_eval_count)
    print("Output tokens:", response.eval_count)
    return _validate_extraction(json.loads(content))


def _record_from_acr(acr: dict, record: Optional[PatientRecord] = None) -> PatientRecord:
    updated = record.model_copy(deep=True) if record else PatientRecord()
    updated.chief_complaint = _as_text(acr.get("chief_complaint")) or None
    date = _as_text(acr.get("occurrence_date"))
    time = _as_text(acr.get("occurrence_time"))
    updated.onset = " ".join(part for part in (date, time) if part) or None

    allergies = []
    if acr.get("allergies_nka"):
        allergies.append("NKA")
    if acr.get("allergies_cno"):
        allergies.append("CNO")
    if acr.get("allergies_other") or acr.get("allergies_details"):
        allergies.extend(
            item.strip() for item in str(acr.get("allergies_details") or "").split(",")
            if item.strip()
        )
        if not allergies:
            allergies.append("Other (see details)")
    updated.allergies = allergies

    medication_rows, vital_rows = [], []
    for row in acr.get("procedures", []):
        code = _as_text(row.get("procedure"))
        when = _as_text(row.get("time")) or None
        drug_name = _as_text(row.get("reading")) if code == "700" else MEDICATION_NAMES.get(code)
        if drug_name:
            medication_rows.append(MedicationAdministered(
                drug=drug_name, dose=_as_text(row.get("dose")),
                route=_as_text(row.get("route")), timestamp=when,
            ))
        glucose = _as_text(row.get("reading")) if code == "025" else ""
        hr = _int_or_none(row.get("pulse_rate"))
        spo2 = _int_or_none(row.get("spo2"))
        gcs = _int_or_none(row.get("gcs"))
        bp = _as_text(row.get("bp")) or None
        if any(value is not None for value in (hr, spo2, gcs, bp)) or glucose:
            vital_rows.append(VitalSign(
                timestamp=when, bp=bp, hr=hr, spo2=spo2, gcs=gcs,
                blood_glucose=glucose or None,
            ))
    if medication_rows:
        updated.medications_administered = medication_rows
    if vital_rows:
        updated.vitals_stream = vital_rows
    return updated


def run_protocol_and_validation_engine(record: PatientRecord) -> PatientRecord:
    """Refresh the form-facing protocol alerts and required-field notices."""
    complaint = (record.chief_complaint or "").lower()
    triggers = []
    if any(term in complaint for term in ("cardiac arrest", "respiratory arrest", "unresponsive")):
        triggers.append({
            "protocol": "CARDIAC / RESPIRATORY ARREST PROTOCOL",
            "status": "URGENT ACTIVE",
            "required_actions": ["Begin CPR / BVM ventilation", "Prepare defibrillator / AED"],
        })
    elif "chest pain" in complaint:
        triggers.append({
            "protocol": "ACS / STEMI Protocol",
            "status": "Active",
            "required_actions": ["12-Lead ECG", "Verify aspirin administration"],
        })
    record.protocol_triggers = triggers
    record.missing_fields = [] if record.allergies else [
        "Allergies not recorded (Required before ED handoff)"
    ]
    return record


def extract_structured_record(raw_transcript: str) -> PatientRecord:
    """Extract an ACR-compatible record for callers that need the model form."""
    return run_protocol_and_validation_engine(
        _record_from_acr(extract_data_with_llm(raw_transcript))
    )


def log_paramedic_feedback(transcript: str, ai_extraction: dict,
                           human_correction: dict, reason: str):
    entry = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "raw_transcript": transcript,
        "ai_predicted_extraction": ai_extraction,
        "human_corrected_extraction": human_correction,
        "correction_type": reason,
    }
    feedback = []
    if FEEDBACK_LOG_FILE.exists():
        feedback = json.loads(FEEDBACK_LOG_FILE.read_text(encoding="utf-8"))
    feedback.append(entry)
    FEEDBACK_LOG_FILE.write_text(json.dumps(feedback, indent=2), encoding="utf-8")


def vet_extraction_with_paramedic(
        transcript: str, extracted_data: dict[str, object]) -> tuple[dict[str, object], str, str]:
    """Review or edit sparse ACR fields in the standalone CLI."""
    extracted_data = _validate_extraction(dict(extracted_data))
    comments = ""
    status = "Accepted"

    def edit_procedure(code: str, field: str, value: str, time_value: str = "") -> None:
        rows = extracted_data.setdefault("procedures", [])
        if not isinstance(rows, list):
            rows = []
            extracted_data["procedures"] = rows
        row = next(
            (item for item in rows
             if isinstance(item, dict) and str(item.get("procedure", "")) == code),
            None,
        )
        if row is None:
            row = {"procedure": code}
            if time_value:
                row["time"] = time_value
            rows.append(row)
        row[field] = value
        if time_value and not row.get("time"):
            row["time"] = time_value

    print("\n" + "=" * 50)
    print("📋 PARAMEDIC REVIEW & VERIFICATION STEP")
    print("=" * 50)
    print(f'Transcript: "{transcript}"\n')
    print("AI Extracted Summary:")
    print(json.dumps(extracted_data, indent=2))
    print("=" * 50)

    while True:
        print("\nReview Actions:")
        print("  [1] Accept AI Extraction")
        print("  [2] Edit Any Field (Name, Age, Gender, Allergies, Complaint, Vitals, Medications)")
        print("  [3] Add Paramedic Clinical Comment")
        print("  [4] Reject Extraction (Keep transcript only)")

        choice = input("Select review action (1-4): ").strip()
        if choice == "1":
            print("[✓] Extraction accepted by paramedic.")
            break
        if choice == "2":
            field = input(
                "Enter field to edit (name, age, gender, allergies, complaint, bp, hr, "
                "spo2, gcs, glucose, medication, or an exact ACR field): "
            ).strip().lower()
            original_extracted = json.loads(json.dumps(extracted_data))

            if field == "name":
                name = input("Enter correct patient name: ").strip()
                parts = name.split(maxsplit=1)
                extracted_data["first_name"] = parts[0] if parts else ""
                extracted_data["last_name"] = parts[1] if len(parts) > 1 else ""
            elif field == "age":
                value = input("Enter correct patient age: ").strip()
                if value and not value.isdigit():
                    print("[!] Age must be a whole number.")
                    continue
                if value:
                    extracted_data["age"] = value
                else:
                    extracted_data["age"] = ""
            elif field == "gender":
                value = input("Enter correct patient gender (M/F or description): ").strip()
                if value:
                    extracted_data["sex"] = value
                else:
                    extracted_data["sex"] = ""
            elif field == "allergies":
                value = input("Enter allergies (comma-separated; NKA/NKDA/CNO supported): ").strip()
                items = [item.strip() for item in value.split(",") if item.strip()]
                extracted_data.pop("allergies_nka", None)
                extracted_data.pop("allergies_cno", None)
                extracted_data.pop("allergies_other", None)
                extracted_data.pop("allergies_details", None)
                if any(item.casefold() in {"nka", "nkda", "no known allergies", "no known drug allergies"}
                       for item in items):
                    extracted_data["allergies_nka"] = True
                else:
                    if any(item.casefold() == "cno" for item in items):
                        extracted_data["allergies_cno"] = True
                    others = [item for item in items if item.casefold() != "cno"]
                    if others:
                        extracted_data["allergies_other"] = True
                        extracted_data["allergies_details"] = ", ".join(others)
                if not items:
                    extracted_data.update({
                        "allergies_nka": False,
                        "allergies_cno": False,
                        "allergies_other": False,
                        "allergies_details": "",
                    })
            elif field == "complaint":
                value = input("Enter correct complaint / status update: ").strip()
                if value:
                    extracted_data["chief_complaint"] = value
                else:
                    extracted_data["chief_complaint"] = ""
            elif field in {"bp", "hr", "spo2", "gcs", "glucose"}:
                value = input(f"Enter correct value for '{field}': ").strip()
                if field in {"hr", "spo2", "gcs"} and value and not value.isdigit():
                    print(f"[!] {field.upper()} must be a whole number.")
                    continue
                procedure_code = "025" if field == "glucose" else "010"
                form_field = "reading" if field == "glucose" else {
                    "bp": "bp", "hr": "pulse_rate", "spo2": "spo2", "gcs": "gcs",
                }[field]
                if value:
                    edit_procedure(
                        procedure_code, form_field, value,
                        input("Enter time (HH:MM, or leave blank): ").strip(),
                    )
                else:
                    rows = extracted_data.get("procedures", [])
                    if isinstance(rows, list):
                        form_field_key = form_field
                        target = next(
                            (item for item in rows
                             if isinstance(item, dict)
                             and str(item.get("procedure", "")) == procedure_code
                             and form_field_key in item),
                            None,
                        )
                        if target is not None:
                            target.pop(form_field_key, None)
                            if set(target) == {"procedure"}:
                                rows.remove(target)
            elif field == "medication":
                drug = input("Enter drug name: ").strip()
                dose = input("Enter dose (e.g. '5 mg'): ").strip()
                route = input("Enter route (e.g. 'nebulizer', 'intramuscular'): ").strip()
                time_value = input("Enter timestamp (or leave blank): ").strip()
                if drug:
                    matches = [
                        code for code, label in MEDICATION_NAMES.items()
                        if label.casefold() == drug.casefold()
                    ]
                    aliases = {
                        "aspirin": "504", "asa": "504", "nitro": "615",
                        "gtn": "615", "ventolin": "650", "dextrose": "530",
                        "d50": "530",
                    }
                    code = matches[0] if matches else aliases.get(drug.casefold(), "700")
                    row = {"procedure": code}
                    if code == "700":
                        row["reading"] = drug
                    for key, value in (("dose", dose), ("route", route), ("time", time_value)):
                        if value:
                            row[key] = value
                    extracted_data.setdefault("procedures", []).append(row)
                else:
                    print("[!] Medication name is required.")
                    continue
            elif field == "procedures":
                value = input("Enter the complete procedures value as JSON array: ").strip()
                try:
                    rows = json.loads(value)
                    if not isinstance(rows, list):
                        raise ValueError("expected a JSON array")
                    _validate_extraction({"procedures": rows})
                except (json.JSONDecodeError, ValueError) as error:
                    print(f"[!] Invalid procedures: {error}")
                    continue
                extracted_data["procedures"] = rows
            elif field in ACR_FIELDS:
                value = input(f"Enter correct value for '{field}' (blank clears it): ").strip()
                if field in CHECKBOX_FIELDS:
                    if value.casefold() not in {"true", "false"}:
                        print("[!] Checkbox values must be true or false.")
                        continue
                    extracted_data[field] = value.casefold() == "true"
                elif value:
                    extracted_data[field] = value
                else:
                    extracted_data[field] = ""
            else:
                print("[!] Unknown field name.")
                continue

            try:
                _validate_extraction(extracted_data)
            except ValueError as error:
                print(f"[!] Edit not applied: {error}")
                extracted_data = original_extracted
                continue
            status = "Modified"
            print("\n[Updated AI Extraction]:")
            print(json.dumps(extracted_data, indent=2))
            log_paramedic_feedback(
                transcript=transcript,
                ai_extraction=original_extracted,
                human_correction=extracted_data,
                reason="Full Field Modification",
            )
        elif choice == "3":
            note = input("Type paramedic clinical comment: ").strip()
            if note:
                comments = note
                print(f'[✓] Comment appended: "{comments}"')
        elif choice == "4":
            original_extracted = json.loads(json.dumps(extracted_data))
            extracted_data = {"incident_history": transcript}
            status = "Manual Overridden"
            log_paramedic_feedback(
                transcript=transcript,
                ai_extraction=original_extracted,
                human_correction=extracted_data,
                reason="Full Rejection",
            )
            print("[🛑] AI extraction rejected. Raw transcript preserved in the ACR incident history.")
            break
        else:
            print("[!] Invalid option. Select 1, 2, 3, or 4.")
    return _validate_extraction(extracted_data), comments, status


def save_record_to_master_json(record: PatientRecord, acr: Optional[dict] = None) -> str:
    """Upsert a CLI encounter in the same JSON layout used by the Flask app."""
    data = json.loads(MASTER_DB_FILE.read_text(encoding="utf-8")) if MASTER_DB_FILE.exists() else {"encounters": []}
    encounters = data.get("encounters", []) if isinstance(data, dict) else data
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    existing = next((item for item in encounters if item.get("encounter_id") == record.encounter_id), None)
    entry = {**record.model_dump(), "acr": acr or {},
             "updated_at": now, "created_at": existing.get("created_at", now) if existing else now}
    if existing:
        existing.update(entry)
    else:
        used = [item.get("incident_no", 441366) for item in encounters
                if isinstance(item.get("incident_no"), int)]
        entry["incident_no"] = max(used + [441366]) + 1
        encounters.append(entry)
    fd, temp_name = tempfile.mkstemp(dir=MASTER_DB_FILE.parent, prefix=".ems_cli_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump({"encounters": encounters}, output, indent=2, ensure_ascii=False)
        os.replace(temp_name, MASTER_DB_FILE)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    print(f"[Saved] {MASTER_DB_FILE} (Incident {entry['incident_no']})")
    return str(MASTER_DB_FILE)


def process_new_audio_event(record: PatientRecord, raw_transcript: str,
                            acr: Optional[dict] = None) -> PatientRecord:
    """Extract, interactively review, and merge one CLI voice update."""
    if acr is None:
        acr = {}
    original = extract_data_with_llm(raw_transcript)
    updates, comments, _ = vet_extraction_with_paramedic(raw_transcript, original)
    if comments:
        previous = _as_text(acr.get("incident_history"))
        acr["incident_history"] = "\n".join(part for part in (previous, comments) if part)
    for key, value in updates.items():
        if key == "procedures":
            acr.setdefault("procedures", []).extend(value)
        else:
            acr[key] = value
    if updates.get("allergies_nka"):
        acr.update({"allergies_cno": False, "allergies_other": False, "allergies_details": ""})
    elif updates.get("allergies_other") or updates.get("allergies_details"):
        acr["allergies_nka"] = False
    updated = run_protocol_and_validation_engine(_record_from_acr(acr, record))
    save_record_to_master_json(updated, acr)
    return updated


def print_ed_handoff_report(record: PatientRecord):
    print("\n" + "🚨" * 20)
    print("EMERGENCY DEPARTMENT PRE-ARRIVAL CLINICAL HANDOFF")
    print("🚨" * 20)
    print(f"ENCOUNTER ID  : {record.encounter_id}")
    print(f"CHIEF COMPLAINT: {record.chief_complaint or 'Not recorded'}")
    print(f"ONSET         : {record.onset or 'Not recorded'}")
    print(f"ALLERGIES     : {', '.join(record.allergies) if record.allergies else 'NOT RECORDED'}")
    print("\nACTIVE PROTOCOL ALERTS:")
    for protocol in record.protocol_triggers:
        print(f"  [{protocol['status']}] {protocol['protocol']}")
        print(f"  Required actions: {', '.join(protocol['required_actions'])}")
    if not record.protocol_triggers:
        print("  None active")
    print("\nVITALS:")
    for vital in record.vitals_stream:
        print(
            f"  {vital.timestamp or 'Time unknown'}: BP {vital.bp or 'N/A'}, "
            f"HR {vital.hr if vital.hr is not None else 'N/A'}, "
            f"SpO2 {vital.spo2 if vital.spo2 is not None else 'N/A'}%, "
            f"GCS {vital.gcs if vital.gcs is not None else 'N/A'}, "
            f"BGL {vital.blood_glucose or 'N/A'}"
        )
    print("\nMEDICATIONS:")
    for medication in record.medications_administered:
        print(f"  {medication.drug} ({medication.dose}, {medication.route})")
    print("\n" + "🚨" * 20 + "\n")


def main():
    """Run the original prompt-driven standalone scribe workflow."""
    from modules.stt import listen_for_speech, transcribe_audio

    record = run_protocol_and_validation_engine(PatientRecord())
    acr = {}
    print(f"=== EMS ENCOUNTER STARTED (ID: {record.encounter_id}) ===")
    save_record_to_master_json(record, acr)
    while not record.paramedic_signed_off:
        print(f"\n--- Transport Phase (Timeline Events Logged: {len(record.vitals_stream) + len(record.medications_administered)}) ---")
        print("Options: [1] Record voice update  [2] View Current ED Summary  [3] Sign & Transmit to ED")
        choice = input("Select an option (1, 2, or 3): ").strip()
        if choice == "1":
            audio = listen_for_speech()
            if audio.size:
                transcript = transcribe_audio(audio)
                print(f'\n[Transcribed Text]\n"{transcript}"\n')
                if transcript.strip():
                    record = process_new_audio_event(record, transcript, acr)
                    print("[Saved] Voice update reviewed and added.")
            else:
                print("[!] No audio captured.")
        elif choice == "2":
            print_ed_handoff_report(record)
        elif choice == "3":
            record.paramedic_signed_off = True
            save_record_to_master_json(record, acr)
            print_ed_handoff_report(record)
            print("=== FINAL SIGNED RECORD TRANSMITTED TO ED ===")
            break
        else:
            print("[!] Invalid option. Select 1, 2, or 3.")


if __name__ == "__main__":
    main()
