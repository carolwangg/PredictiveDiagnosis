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
ACR_FIELDS = {
    "first_name", "last_name", "age", "sex", "mail_street_no", "mail_street_name",
    "mail_city", "mail_province", "mail_postal_code", "pickup_location",
    "call_date", "occurrence_date",
    "occurrence_time", "chief_complaint", "incident_history", "remarks",
    "allergies_nka", "allergies_cno", "allergies_other", "allergies_details",
    "history_previously_healthy", "history_cardiac", "history_respiratory",
    "history_seizure", "history_diabetes", "history_psychiatric",
    "history_anaphylaxis", "history_cancer", "history_stroke_tia",
    "history_hypertension", "history_other", "history_cno", "history_details",
    "meds_nitrates", "meds_asa", "meds_salbutamol", "meds_furosemide",
    "meds_insulin_oral_diabetic", "meds_pde_inhibitors", "meds_other",
    "meds_none", "meds_cno", "medications_details", "general_appearance",
    "blood_thinners", "skin_colour_flushed", "skin_colour_pale",
    "skin_colour_cyanosis", "skin_colour_jaundice", "skin_colour_unremarkable",
    "skin_condition_dry", "skin_condition_clammy", "skin_condition_diaphoretic",
    "skin_condition_unremarkable", "air_entry_bilateral", "air_entry_decreased_r",
    "air_entry_decreased_l", "breath_clear", "breath_wheezes", "breath_crackles",
    "breath_rub", "breath_absent", "abdomen_soft", "abdomen_rigid",
    "abdomen_distended", "abdomen_tender", "abdomen_mass", "abdomen_pulsatile",
    "abdomen_ru", "abdomen_lu", "abdomen_ll", "abdomen_rl", "abdomen_center",
    "back_pelvis_unremarkable", "extremities_unremarkable", "peripheral_edema",
    "pedal_pulse", "primary_problem", "problem_code",
}
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
    unknown = set(extraction) - ACR_FIELDS - {"procedures"}
    if unknown:
        raise ValueError("Scribe returned unsupported ACR fields: " + ", ".join(sorted(unknown)))
    for key in list(extraction):
        value = extraction[key]
        if value is None or value == "":
            del extraction[key]
        elif key in CHECKBOX_FIELDS:
            if not isinstance(value, bool):
                raise ValueError(f"Scribe field {key} must be a boolean.")
        elif key != "procedures" and (
                isinstance(value, bool) or not isinstance(value, (str, int, float))):
            raise ValueError(f"Scribe field {key} must be a scalar.")
    if "procedures" in extraction:
        procedures = extraction["procedures"]
        if not isinstance(procedures, list):
            raise ValueError("Scribe procedures must be a list.")
        normalized_rows = []
        for row in procedures:
            if not isinstance(row, dict) or set(row) - PROCEDURE_FIELDS:
                raise ValueError("Scribe returned a malformed procedure row.")
            normalized = {}
            for key, value in row.items():
                if value is None or value == "":
                    continue
                if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                    raise ValueError(f"Procedure field {key} must be a scalar.")
                normalized[key] = str(value)
            if normalized:
                normalized_rows.append(normalized)
        extraction["procedures"] = normalized_rows
    return extraction


def extract_data_with_llm(raw_transcript: str) -> dict:
    """Return sparse, flat ACR fields and procedure rows from a voice transcript."""
    if not raw_transcript.strip():
        raise ValueError("Cannot extract ACR fields from an empty transcript.")

    prompt = f"""
You extract only facts explicitly stated in a paramedic's prehospital report.
Return one valid JSON object using the exact flat field names used by the Ambulance
Call Report (ACR) web form. Do not add a wrapper object or explanatory prose.
Only include fields supported by this transcript. Omit unknown or unstated fields;
never guess, infer a negative finding, or overwrite a value by returning an empty value.

The available form fields are:
demographics: first_name, last_name, age, sex, mail_street_no, mail_street_name,
mail_city, mail_province, mail_postal_code, pickup_location, call_date, occurrence_date,
occurrence_time;
clinical: chief_complaint, incident_history, remarks, primary_problem, problem_code;
allergies: allergies_nka, allergies_cno, allergies_other, allergies_details;
medical history: history_previously_healthy, history_cardiac, history_respiratory,
history_seizure, history_diabetes, history_psychiatric, history_anaphylaxis,
history_cancer, history_stroke_tia, history_hypertension, history_other,
history_cno, history_details;
medication checkboxes: meds_nitrates, meds_asa, meds_salbutamol, meds_furosemide,
meds_insulin_oral_diabetic, meds_pde_inhibitors, meds_other, meds_none, meds_cno,
medications_details;
exam: general_appearance, blood_thinners, skin_colour_flushed, skin_colour_pale,
skin_colour_cyanosis, skin_colour_jaundice, skin_colour_unremarkable,
skin_condition_dry, skin_condition_clammy, skin_condition_diaphoretic,
skin_condition_unremarkable, air_entry_bilateral, air_entry_decreased_r,
air_entry_decreased_l, breath_clear, breath_wheezes, breath_crackles, breath_rub,
breath_absent, abdomen_soft, abdomen_rigid, abdomen_distended, abdomen_tender,
abdomen_mass, abdomen_pulsatile, abdomen_ru, abdomen_lu, abdomen_ll, abdomen_rl,
abdomen_center, back_pelvis_unremarkable, extremities_unremarkable,
peripheral_edema, pedal_pulse;
and procedures: procedures, a list of rows with only these keys:
time, procedure, crew, reading, result_code, resp_rate, pulse_rate, bp, temp,
spo2, pupil_r, pupil_l, dose, route, gcs, pain.

Use ACR date format YYYY-MM-DD and time format HH:MM. ACR checkboxes must be
true only when the transcript explicitly supports them. Use the value "M" or "F"
for sex when clear, otherwise the stated description. For an administered drug,
add a procedure row with its medication procedure code when known; use code "700"
for another drug and put its name in reading. Use code "010" for a row of vital
signs and "025" for a blood glucose reading. Preserve units in glucose readings
and doses. For a vitals row, use ACR names (pulse_rate, bp, spo2, gcs, resp_rate).
Keep incident_history concise, chronological, and factual. Do not create empty
procedure rows. If nothing can be safely extracted, return {{}}.

Transcript:
{json.dumps(raw_transcript)}
"""
    from ollama import chat

    response = chat(
        model="llama3:latest",
        messages=[{"role": "user", "content": prompt}],
        format="json",
    )
    content = response["message"]["content"]
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


def vet_extraction_with_paramedic(transcript: str, extracted_data: dict) -> tuple[dict, str, str]:
    """Interactive review for the standalone CLI; the web flow never prompts."""
    extracted_data = dict(extracted_data)
    original_extracted = dict(extracted_data)
    comments = ""
    status = "Accepted"
    print("\n" + "=" * 50)
    print("PARAMEDIC REVIEW & VERIFICATION")
    print("=" * 50)
    print(f'Transcript: "{transcript}"\n')
    print(json.dumps(extracted_data, indent=2))
    while True:
        print("\n[1] Accept  [2] Edit ACR field  [3] Add comment  [4] Reject extraction")
        choice = input("Select review action (1-4): ").strip()
        if choice == "1":
            break
        if choice == "2":
            field = input("Enter exact ACR field name: ").strip()
            if field in ACR_FIELDS:
                value = input(f"Enter value for {field}: ").strip()
                if field in CHECKBOX_FIELDS:
                    if value.lower() not in ("true", "false"):
                        print("Checkbox values must be true or false.")
                        continue
                    extracted_data[field] = value.lower() == "true"
                else:
                    extracted_data[field] = value
                status = "Modified"
            elif field == "procedures":
                print("Edit procedures in the ACR form after this CLI session.")
            else:
                print("That is not an editable ACR field.")
        elif choice == "3":
            comments = input("Type paramedic clinical note/comment: ").strip()
        elif choice == "4":
            extracted_data = {
                "incident_history": "Raw voice report (extraction rejected): " + transcript
            }
            status = "Manual Overridden"
            break
        else:
            print("[!] Invalid option. Select 1, 2, 3, or 4.")
    if status == "Modified":
        log_paramedic_feedback(transcript, original_extracted, extracted_data, "Field Modification")
    elif status == "Manual Overridden":
        log_paramedic_feedback(transcript, original_extracted, extracted_data, "Full Rejection")
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
        elif value not in (None, "", False):
            acr[key] = value
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
