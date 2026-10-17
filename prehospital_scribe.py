from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field
import uuid
import json


# ==========================================
# 1. STRICT DATA MODELS (Pydantic Schemas)
# ==========================================

class VitalSign(BaseModel):
    timestamp: str = Field(..., description="Time vitals were recorded, e.g., '14:40'")
    bp: Optional[str] = Field(None, description="Blood Pressure, e.g., '140/90'")
    hr: Optional[int] = Field(None, description="Heart Rate in BPM")
    spo2: Optional[int] = Field(None, description="Oxygen Saturation percentage")
    gcs: Optional[int] = Field(None, description="Glasgow Coma Scale total (3-15)")
    blood_glucose: Optional[str] = Field(None, description="Blood glucose level if checked")


class MedicationAdministered(BaseModel):
    drug: str
    dose: str
    route: str
    timestamp: str


class PatientRecord(BaseModel):
    encounter_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    chief_complaint: Optional[str] = None
    onset: Optional[str] = None
    allergies: List[str] = Field(default_factory=list)
    medications_administered: List[MedicationAdministered] = Field(default_factory=list)
    vitals_stream: List[VitalSign] = Field(default_factory=list)
    protocol_triggers: List[dict] = Field(default_factory=list)
    missing_fields: List[str] = Field(default_factory=list)
    paramedic_signed_off: bool = False


# ==========================================
# 2. EXTRACTION ENGINE SIMULATOR
# ==========================================
# In production, this replaces raw text with structured output
# using an LLM constrained by the PatientRecord JSON schema.

def extract_structured_record(raw_transcript: str) -> PatientRecord:
    """
    Simulates parsing unstructured paramedic voice notes into
    a strict clinical schema.
    """
    print(f"[*] Processing raw audio transcript: \"{raw_transcript}\"\n")

    # Mocked extraction output based on the transcript sample
    record = PatientRecord(
        chief_complaint="Acute chest pain",
        onset="20 minutes ago",
        allergies=[],  # Intentional missing data for rule engine test
        medications_administered=[
            MedicationAdministered(
                drug="Aspirin",
                dose="160 mg",
                route="PO",
                timestamp="14:42"
            )
        ],
        vitals_stream=[
            VitalSign(
                timestamp="14:40",
                bp="140/90",
                hr=110,
                spo2=94,
                gcs=15,
                blood_glucose=None  # Intentional missing vital
            )
        ]
    )
    return record


# ==========================================
# 3. DETERMINISTIC RULE & PROTOCOL ENGINE
# ==========================================

def run_protocol_and_validation_engine(record: PatientRecord) -> PatientRecord:
    """
    Applies deterministic clinical logic (checklists, protocol triggers,
    and missing data flags) instead of probabilistic AI guessing.
    """
    missing = []
    triggers = []

    # Check for missing critical safety fields
    if not record.allergies:
        missing.append("Allergies not recorded (Required before handoff)")

    if not record.vitals_stream or record.vitals_stream[-1].blood_glucose is None:
        missing.append("Blood glucose missing for chest pain protocol")

    # Protocol-based deterministic rules
    complaint = (record.chief_complaint or "").lower()
    if "chest pain" in complaint or "cardiac" in complaint:
        triggers.append({
            "protocol": "STEMI / Acute Coronary Syndrome",
            "status": "Active",
            "required_actions": ["12-Lead ECG transmission", "Aspirin confirmed", "Notify ED receiving team"]
        })

    record.missing_fields = missing
    record.protocol_triggers = triggers
    return record


# ==========================================
# 4. EXECUTION DEMO
# ==========================================

if __name__ == "__main__":
    # Simulated chaotic voice transcript from a paramedic inside a moving ambulance
    raw_audio_transcript = (
        "Patient is a 65-year-old male complaining of acute chest pain starting "
        "about 20 minutes ago. Vitals at 14:40: BP 140 over 90, heart rate 110, "
        "SpO2 94 percent, GCS 15. Administered 160 milligrams of Aspirin orally at 14:42."
    )

    # Step 1: Extract into structured schema
    structured_record = extract_structured_record(raw_audio_transcript)

    # Step 2: Run safety checklists and protocol engines
    validated_record = run_protocol_and_validation_engine(structured_record)

    # Step 3: Output results for paramedic review/sign-off
    print("=== VALIDATED PRE-ARRIVAL SUMMARY ===")
    print(json.dumps(validated_record.model_dump(), indent=4))

    # Step 4: Simulate Paramedic Sign-off
    print("\n[Action] Paramedic reviews alerts, confirms data, and taps 'Sign & Transmit to ED'.")
    validated_record.paramedic_signed_off = True
    print(f"Record Signed Status: {validated_record.paramedic_signed_off}")