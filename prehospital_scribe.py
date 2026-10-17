# prehospital_scribe.py
from typing import List, Optional
from pydantic import BaseModel, Field
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
    timestamp: str = Field(..., description="Time vitals were recorded")
    bp: Optional[str] = Field(None, description="Blood Pressure")
    hr: Optional[int] = Field(None, description="Heart Rate in BPM")
    spo2: Optional[int] = Field(None, description="Oxygen Saturation percentage")
    gcs: Optional[int] = Field(None, description="Glasgow Coma Scale total (3-15)")
    blood_glucose: Optional[str] = Field(None, description="Blood glucose level")


class MedicationAdministered(BaseModel):
    drug: str
    dose: str
    route: str
    timestamp: str


class ClinicalEvent(BaseModel):
    event_sequence: int
    timestamp: str
    raw_transcript: str
    noted_complaint: Optional[str] = None
    new_vitals: List[VitalSign] = Field(default_factory=list)
    new_medications: List[MedicationAdministered] = Field(default_factory=list)


class PatientRecord(BaseModel):
    encounter_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    patient_age: Optional[int] = None
    patient_gender: Optional[str] = None
    allergies: List[str] = Field(default_factory=list)

    # Chronological timeline of events during the call
    timeline: List[ClinicalEvent] = Field(default_factory=list)

    # Active protocols and warnings based on the latest state
    active_protocols: List[dict] = Field(default_factory=list)
    missing_fields: List[str] = Field(default_factory=list)
    paramedic_signed_off: bool = False


# ==========================================
# 2. MASTER JSON DATABASE STORAGE HELPER
# ==========================================

MASTER_DB_FILE = "ems_encounters.json"


def save_record_to_master_json(record: PatientRecord) -> str:
    """Saves or updates the current patient record inside the central master JSON database."""
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


# ==========================================
# 3. LOCAL LLM STRUCTURED EXTRACTION ENGINE
# ==========================================

def extract_data_with_llm(raw_transcript: str) -> dict:
    """Uses a local LLM (Ollama) to extract structured clinical fields from raw speech text."""
    print("[🤖] Running local LLM extraction...")

    prompt = f"""
    You are a medical data extraction assistant for prehospital EMS documentation. 
    Analyze the following paramedic voice transcript and extract structured clinical fields into strict JSON format.

    Transcript: "{raw_transcript}"

    Extract and return ONLY a valid JSON object with the following keys:
    - "patient_age": integer or null
    - "patient_gender": string (e.g. "Male", "Female") or null
    - "allergies": list of strings (or empty list if none)
    - "noted_complaint": string summary of chief complaint or status update
    - "new_vitals": list of objects containing any recorded vitals, each with:
      - "timestamp": string (e.g. "14:40") or null
      - "bp": string or null
      - "hr": integer or null
      - "spo2": integer or null
      - "gcs": integer or null
      - "blood_glucose": string or null
    - "new_medications": list of objects containing any administered medications, each with:
      - "drug": string
      - "dose": string
      - "route": string
      - "timestamp": string or null
    ""

    try:
        response = ollama.chat(
            model='llama3',  # Make sure Ollama is running and llama3 is installed locally
            messages=[{'role': 'user', 'content': prompt}],
            format='json'  # Forces Ollama to output valid JSON
        )  # <-- Fixed: closed with parenthesis instead of brace

        return json.loads(response['message']['content'])
    except Exception as e:
        print(f"[!] LLM Extraction Error: {e}")
        return {}

# ==========================================
# 4. EVENT PROCESSOR & RULE ENGINE
# ==========================================

def process_new_audio_event(record: PatientRecord, raw_transcript: str) -> PatientRecord:
    """Processes raw audio transcript via LLM, appends to timeline, and updates protocols."""
    current_time = datetime.now().strftime("%H:%M:%S")
    event_num = len(record.timeline) + 1

    # 1. Extract structured data using local LLM
    extracted_data = extract_data_with_llm(raw_transcript)

    # Update global patient demographics if detected and not already set
    if not record.patient_age and extracted_data.get("patient_age"):
        record.patient_age = extracted_data.get("patient_age")
    if not record.patient_gender and extracted_data.get("patient_gender"):
        record.patient_gender = extracted_data.get("patient_gender")

    # Merge allergies if found
    for allergy in extracted_data.get("allergies", []):
        if allergy not in record.allergies:
            record.allergies.append(allergy)

    # Build new vitals & medication sub-objects
    vitals_list = [VitalSign(**v) for v in extracted_data.get("new_vitals", [])]
    meds_list = [MedicationAdministered(**m) for m in extracted_data.get("new_medications", [])]

    # Create chronological clinical event
    new_event = ClinicalEvent(
        event_sequence=event_num,
        timestamp=current_time,
        raw_transcript=raw_transcript,
        noted_complaint=extracted_data.get("noted_complaint", "Status update"),
        new_vitals=vitals_list,
        new_medications=meds_list
    )

    record.timeline.append(new_event)

    # 2. Protocol & Safety Rule Engine
    triggers = []
    for event in record.timeline:
        c = (event.noted_complaint or "").lower()
        # Also check transcripts across timeline for safety triggers
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

    # Deduplicate triggers safely
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

    # 3. Save to Master JSON database
    save_record_to_master_json(record)

    return record


# ==========================================
# 5. MAIN EXECUTION LOOP
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
                    print("[✓] Event successfully processed by LLM and saved to master database.")
            else:
                print("[!] No audio captured.")

        elif choice == "2":
            print("\n=== CURRENT LIVE PRE-ARRIVAL SUMMARY ===")
            print(json.dumps(record.model_dump(), indent=4))

        elif choice == "3":
            print("\n[Action] Paramedic finalizes review and signs off.")
            record.paramedic_signed_off = True
            save_record_to_master_json(record)
            print("=== FINAL SIGNED RECORD TRANSMITTED TO ED ===")
            print(json.dumps(record.model_dump(), indent=4))
            break
        else:
            print("[!] Invalid option. Select 1, 2, or 3.")
