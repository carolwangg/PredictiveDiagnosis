# prehospital_scribe.py
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator
import uuid
import json
import os
from datetime import datetime
import ollama

# Import the Push-to-Talk STT module functions
from modules.stt import listen_for_speech, transcribe_audio


# ==========================================
# 1. STRICT CHRONOLOGICAL DATA MODELS
# ==========================================

class VitalSign(BaseModel):
    timestamp: Optional[str] = Field(None, description="Time vitals were recorded")
    bp: Optional[str] = Field(None, description="Blood Pressure")
    hr: Optional[int] = Field(None, description="Heart Rate in BPM")
    spo2: Optional[int] = Field(None, description="Oxygen Saturation percentage")
    gcs: Optional[int] = Field(None, description="Glasgow Coma Scale total (3-15)")
    blood_glucose: Optional[str] = Field(None, description="Blood glucose level")


class MedicationAdministered(BaseModel):
    drug: str
    dose: str
    route: str
    timestamp: Optional[str] = None

    @field_validator('dose', 'timestamp', mode='before')
    @classmethod
    def coerce_to_string(cls, v):
        """Automatically converts numbers or other types to string if the LLM outputs them."""
        if v is not None:
            return str(v)
        return v


class ClinicalEvent(BaseModel):
    event_sequence: int
    timestamp: str
    raw_transcript: str
    noted_complaint: Optional[str] = None
    new_vitals: List[VitalSign] = Field(default_factory=list)
    new_medications: List[MedicationAdministered] = Field(default_factory=list)
    paramedic_comments: Optional[str] = None
    verification_status: str = "Accepted"  # Accepted, Modified, or Manual Overridden


class PatientRecord(BaseModel):
    encounter_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    patient_name: Optional[str] = None
    patient_age: Optional[int] = None
    patient_gender: Optional[str] = None
    allergies: List[str] = Field(default_factory=list)

    timeline: List[ClinicalEvent] = Field(default_factory=list)

    active_protocols: List[dict] = Field(default_factory=list)
    missing_fields: List[str] = Field(default_factory=list)
    paramedic_signed_off: bool = False


# ==========================================
# 2. MASTER JSON DATABASE & FEEDBACK LOGGERS
# ==========================================

MASTER_DB_FILE = "ems_encounters.json"
FEEDBACK_LOG_FILE = "feedback_log.json"


def save_record_to_master_json(record: PatientRecord) -> str:
    """Saves or updates the current patient record inside the master JSON database."""
    database = {}
    if os.path.exists(MASTER_DB_FILE):
        try:
            with open(MASTER_DB_FILE, "r", encoding="utf-8") as f:
                database = json.load(f)
        except json.JSONDecodeError:
            database = {}

    database[record.encounter_id] = record.model_dump()

    with open(MASTER_DB_FILE, "w", encoding="utf-8") as f:
        json.dump(database, f, indent=4)

    print(f"[💾] Master database updated -> {MASTER_DB_FILE} (Encounter: {record.encounter_id[:8]})")
    return MASTER_DB_FILE


def log_paramedic_feedback(transcript: str, ai_extraction: dict, human_correction: dict, reason: str):
    """
    Logs paramedic edits and rejections. This dataset feeds back into the AI pipeline
    for fine-tuning and benchmark evaluations.
    """
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "raw_transcript": transcript,
        "ai_predicted_extraction": ai_extraction,
        "human_corrected_extraction": human_correction,
        "correction_type": reason
    }

    feedback_data = []
    if os.path.exists(FEEDBACK_LOG_FILE):
        try:
            with open(FEEDBACK_LOG_FILE, "r", encoding="utf-8") as f:
                feedback_data = json.load(f)
        except json.JSONDecodeError:
            feedback_data = []

    feedback_data.append(log_entry)

    with open(FEEDBACK_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(feedback_data, f, indent=4)

    print(f"[🔁 FEEDBACK LOGGED] Decision saved to {FEEDBACK_LOG_FILE} for AI continuous training.")


# ==========================================
# 3. LOCAL LLM STRUCTURED EXTRACTION ENGINE
# ==========================================

def extract_data_with_llm(raw_transcript: str) -> dict:
    """Uses local Ollama to extract structured clinical fields."""
    print("[🤖] Running local LLM extraction...")

    prompt = f"""
    You are an expert medical data extraction assistant for prehospital EMS documentation. 
    Analyze the following paramedic voice transcript and extract structured clinical fields into strict JSON format.

    Transcript: "{raw_transcript}"

    Strict Extraction Rules:
    - "patient_name": string full name (e.g. "Jane Smith") or null if not mentioned.
    - "patient_age": integer age or null.
    - "patient_gender": "Male", "Female", or null.
    - "allergies": list of specific allergies. If transcript states "no known drug allergies" or "NKDA", include "No Known Allergies".
    - "noted_complaint": short summary of chief complaint or status update.
    - "new_vitals": list of objects containing:
      - "timestamp": string time or null.
      - "bp": string blood pressure ONLY (e.g., "120/80") or null.
      - "hr": integer heart rate or null.
      - "spo2": integer oxygen saturation or null.
      - "gcs": integer GCS total (3-15) or null.
      - "blood_glucose": string blood glucose with units or null.
    - "new_medications": list of objects (drug, dose, route, timestamp).

    Return ONLY a valid JSON object matching these keys.
    """

    try:
        response = ollama.chat(
            model='llama3:latest',
            messages=[{'role': 'user', 'content': prompt}],
            format='json'
        )
        return json.loads(response['message']['content'])
    except Exception as e:
        print(f"[!] LLM Extraction Error: {e}")
        return {}


# ==========================================
# 4. PARAMEDIC REVIEW & VETTING INTERFACE
# ==========================================

def vet_extraction_with_paramedic(transcript: str, extracted_data: dict) -> tuple[dict, str, str]:
    """
    Displays extracted AI data to the paramedic and allows interactive review
    (Accept, Edit, Add Comment, or Reject).
    """
    print("\n" + "=" * 50)
    print("📋 PARAMEDIC REVIEW & VERIFICATION STEP")
    print("=" * 50)
    print(f"Transcript: \"{transcript}\"\n")
    print("AI Extracted Summary:")
    print(json.dumps(extracted_data, indent=2))
    print("=" * 50)

    comments = ""
    status = "Accepted"

    while True:
        print("\nReview Actions:")
        print("  [1] Accept AI Extraction")
        print("  [2] Edit Extracted Fields")
        print("  [3] Add Paramedic Clinical Comment")
        print("  [4] Reject Extraction (Keep transcript only)")

        choice = input("Select review action (1-4): ").strip()

        if choice == "1":
            print("[✓] Extraction accepted by paramedic.")
            break

        elif choice == "2":
            status = "Modified"
            original_extracted = json.loads(json.dumps(extracted_data))

            field = input(
                "Enter field to edit (name, age, gender, complaint, bp, hr, spo2, gcs, glucose): ").strip().lower()
            val = input(f"Enter correct value for '{field}': ").strip()

            if field == "name":
                extracted_data["patient_name"] = val
            elif field == "age":
                extracted_data["patient_age"] = int(val) if val.isdigit() else val
            elif field == "gender":
                extracted_data["patient_gender"] = val
            elif field == "complaint":
                extracted_data["noted_complaint"] = val
            elif field in ["bp", "hr", "spo2", "gcs", "glucose"]:
                if not extracted_data.get("new_vitals"):
                    extracted_data["new_vitals"] = [{}]

                v_target = extracted_data["new_vitals"][0]
                if field == "bp":
                    v_target["bp"] = val
                elif field == "hr":
                    v_target["hr"] = int(val) if val.isdigit() else val
                elif field == "spo2":
                    v_target["spo2"] = int(val) if val.isdigit() else val
                elif field == "gcs":
                    v_target["gcs"] = int(val) if val.isdigit() else val
                elif field == "glucose":
                    v_target["blood_glucose"] = val

            print("\n[Updated AI Extraction]:")
            print(json.dumps(extracted_data, indent=2))

            log_paramedic_feedback(
                transcript=transcript,
                ai_extraction=original_extracted,
                human_correction=extracted_data,
                reason="Field Modification"
            )

        elif choice == "3":
            note = input("Type paramedic clinical note/comment: ").strip()
            if note:
                comments = note
                print(f"[✓] Comment appended: \"{comments}\"")

        elif choice == "4":
            status = "Manual Overridden"
            original_extracted = json.loads(json.dumps(extracted_data))

            extracted_data = {
                "patient_name": None,
                "patient_age": None,
                "patient_gender": None,
                "allergies": [],
                "noted_complaint": "Raw Voice Report (Extraction Rejected)",
                "new_vitals": [],
                "new_medications": []
            }

            log_paramedic_feedback(
                transcript=transcript,
                ai_extraction=original_extracted,
                human_correction=extracted_data,
                reason="Full Rejection"
            )
            print("[🛑] AI Extraction rejected. Raw transcript preserved in record.")
            break

        else:
            print("[!] Invalid option. Select 1, 2, 3, or 4.")

    return extracted_data, comments, status


# ==========================================
# 5. EVENT PROCESSOR & RULE ENGINE
# ==========================================

def process_new_audio_event(record: PatientRecord, raw_transcript: str) -> PatientRecord:
    """Processes transcript via LLM, submits for paramedic vetting, and updates protocols."""
    current_time = datetime.now().strftime("%H:%M:%S")
    event_num = len(record.timeline) + 1

    # 1. AI Extraction
    raw_extracted = extract_data_with_llm(raw_transcript)

    # 2. Human Vetting Step
    vetted_data, paramedic_comments, verification_status = vet_extraction_with_paramedic(
        raw_transcript, raw_extracted
    )

    # 3. Apply Vetted Data to Global Demographics
    if not record.patient_name and vetted_data.get("patient_name"):
        record.patient_name = vetted_data.get("patient_name")
    if not record.patient_age and vetted_data.get("patient_age"):
        record.patient_age = vetted_data.get("patient_age")
    if not record.patient_gender and vetted_data.get("patient_gender"):
        record.patient_gender = vetted_data.get("patient_gender")

    for allergy in vetted_data.get("allergies", []):
        if allergy not in record.allergies:
            record.allergies.append(allergy)

    # 4. Build Structured Timeline Sub-objects
    vitals_list = []
    for v in vetted_data.get("new_vitals", []):
        if not v.get("timestamp"):
            v["timestamp"] = current_time
        vitals_list.append(VitalSign(**v))

    meds_list = [MedicationAdministered(**m) for m in vetted_data.get("new_medications", [])]

    # Create Event
    new_event = ClinicalEvent(
        event_sequence=event_num,
        timestamp=current_time,
        raw_transcript=raw_transcript,
        noted_complaint=vetted_data.get("noted_complaint", "Status update"),
        new_vitals=vitals_list,
        new_medications=meds_list,
        paramedic_comments=paramedic_comments,
        verification_status=verification_status
    )

    record.timeline.append(new_event)

    # 5. Protocol & Safety Rule Engine
    triggers = []
    for event in record.timeline:
        c = (event.noted_complaint or "").lower()
        t_text = event.raw_transcript.lower()

        if "stops breathing" in c or "arrest" in c or "unresponsive" in t_text or "stopped breathing" in t_text:
            triggers.append({
                "protocol": "CARDIAC / RESPIRATORY ARREST PROTOCOL",
                "status": "URGENT ACTIVE",
                "required_actions": ["Begin CPR / BVM ventilation", "Prepare defibrillator / AED",
                                     "Trauma bay red activation"]
            })
        elif "chest pain" in c or "chest pain" in t_text:
            triggers.append({
                "protocol": "ACS / STEMI Protocol",
                "status": "Active",
                "required_actions": ["12-Lead ECG", "Aspirin administration verified"]
            })

    unique_triggers = []
    for trigger in triggers:
        if trigger not in unique_triggers:
            unique_triggers.append(trigger)
    record.active_protocols = unique_triggers

    # Check missing fields
    missing = []
    if not record.allergies:
        missing.append("Allergies not recorded (Required before ED handoff)")
    record.missing_fields = missing

    # Save to Master JSON
    save_record_to_master_json(record)

    return record


# ==========================================
# 6. EMERGENCY DEPARTMENT HANDOFF REPORT
# ==========================================

def print_ed_handoff_report(record: PatientRecord):
    """Formats the validated encounter into an immediate clinical pre-arrival report for ED doctors."""
    print("\n" + "🚨" * 25)
    print("🏥 EMERGENCY DEPARTMENT PRE-ARRIVAL CLINICAL HANDOFF")
    print("🚨" * 25)
    print(f"ENCOUNTER ID  : {record.encounter_id}")
    print(f"PATIENT NAME  : {record.patient_name or 'Unidentified / Unknown'}")
    print(f"PATIENT DEMO  : {record.patient_age or 'Unknown'} Y/O | {record.patient_gender or 'Unknown'}")
    print(f"ALLERGIES     : {', '.join(record.allergies) if record.allergies else 'NOT RECORDED'}")

    print("\nACTIVE PROTOCOL ALERTS:")
    if record.active_protocols:
        for p in record.active_protocols:
            print(f"  ⚠️ [{p['status']}] {p['protocol']}")
            print(f"     Required Actions: {', '.join(p['required_actions'])}")
    else:
        print("  None Active")

    print("\nCHRONOLOGICAL TIMELINE OF EVENTS:")
    for event in record.timeline:
        print(f"\n  ⏱️ Event #{event.event_sequence} [{event.timestamp}] (Status: {event.verification_status})")
        print(f"     Complaint   : {event.noted_complaint}")
        if event.new_vitals:
            v = event.new_vitals[0]
            print(
                f"     Vitals      : BP {v.bp or 'N/A'} | HR {v.hr or 'N/A'} | SpO2 {v.spo2 or 'N/A'}% | GCS {v.gcs or 'N/A'} | BGL {v.blood_glucose or 'N/A'}")
        if event.new_medications:
            m = event.new_medications[0]
            print(f"     Medication  : {m.drug} ({m.dose}, {m.route}) @ {m.timestamp or 'N/A'}")
        if event.paramedic_comments:
            print(f"     Paramedic Note: \"{event.paramedic_comments}\"")

    print("\n" + "🚨" * 25 + "\n")


# ==========================================
# 7. MAIN EXECUTION LOOP
# ==========================================

if __name__ == "__main__":
    record = PatientRecord()
    print(f"=== EMS ENCOUNTER STARTED (ID: {record.encounter_id}) ===")
    save_record_to_master_json(record)

    while not record.paramedic_signed_off:
        print(f"\n--- Transport Phase (Timeline Events Logged: {len(record.timeline)}) ---")
        print("Options: [1] Record voice update  [2] View Current ED Summary  [3] Sign & Transmit to ED")
        choice = input("Select an option (1, 2, or 3): ").strip()

        if choice == "1":
            audio_array = listen_for_speech()
            if audio_array.size > 0:
                raw_transcript = transcribe_audio(audio_array)
                print(f"\n[📝] Transcribed Text:\n\"{raw_transcript}\"\n")

                if raw_transcript.strip():
                    record = process_new_audio_event(record, raw_transcript)
                    print("[✓] Event successfully vetted and saved to master database.")
            else:
                print("[!] No audio captured.")

        elif choice == "2":
            print_ed_handoff_report(record)

        elif choice == "3":
            print("\n[Action] Paramedic finalizes review and signs off.")
            record.paramedic_signed_off = True
            save_record_to_master_json(record)

            print_ed_handoff_report(record)
            print("=== FINAL SIGNED RECORD TRANSMITTED TO ED ===")
            break
        else:
            print("[!] Invalid option. Select 1, 2, or 3.")
