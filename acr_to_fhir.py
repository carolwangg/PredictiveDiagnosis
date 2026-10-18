from copy import deepcopy
from datetime import datetime
from typing import Any
import re


def acr_to_fhir(
    acr: dict[str, Any],
    *,
    patient_id: str = "patient-1",
    encounter_id: str = "encounter-1",
) -> dict[str, Any]:
    """
    Convert ACR JSON into a FHIR R4 Bundle.

    Supports common fields for:
      - Patient
      - Encounter
      - Chief complaint
      - Vital signs
      - Medical history / conditions
      - Allergies
      - Administered medications
      - Procedures

    Unknown fields are preserved in an extension-like text resource
    only when explicitly configured; they are not silently interpreted.

    Adapt FIELD_ALIASES and the collection mappings to your actual ACR schema.
    """

    if not isinstance(acr, dict):
        raise TypeError("acr must be a dictionary")

    resources: list[dict[str, Any]] = []

    def present(value: Any) -> bool:
        return value is not None and value != "" and value != []

    def first(*keys: str) -> Any:
        """Return the first present value without inventing defaults."""
        for key in keys:
            value = acr.get(key)
            if present(value):
                return value
        return None

    def add(resource: dict[str, Any]) -> None:
        resources.append(resource)

    def reference(resource_type: str, resource_id: str) -> dict:
        return {"reference": f"{resource_type}/{resource_id}"}

    def concept(text: str) -> dict:
        # Text-only CodeableConcept: do not invent a clinical code.
        return {"text": str(text)}

    def parse_number(value: Any) -> int | float | None:
        if isinstance(value, bool):
            return None

        if isinstance(value, (int, float)):
            return value

        if isinstance(value, str):
            match = re.fullmatch(
                r"\s*(-?\d+(?:\.\d+)?)\s*",
                value,
            )
            if match:
                number = float(match.group(1))
                return int(number) if number.is_integer() else number

        return None

    # ---------------------------------------------------------
    # 1. Patient
    # ---------------------------------------------------------

    patient: dict[str, Any] = {
        "resourceType": "Patient",
        "id": patient_id,
    }

    name = first("patient_name", "name", "full_name")
    if not name:
        name_parts = [
            str(value).strip()
            for value in (acr.get("first_name"), acr.get("last_name"))
            if present(value)
        ]
        name = " ".join(name_parts) or None
    if name:
        parts = str(name).strip().split()
        patient["name"] = [{
            "text": str(name),
            "given": parts[:-1] if len(parts) > 1 else parts,
            **({"family": parts[-1]} if len(parts) > 1 else {}),
        }]

    birth_date = first("birth_date", "date_of_birth", "dob")
    if birth_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(birth_date)):
        try:
            datetime.strptime(str(birth_date), "%Y-%m-%d")
            patient["birthDate"] = str(birth_date)
        except ValueError:
            pass

    gender = first("gender", "sex")
    if gender:
        gender_map = {
            "male": "male",
            "m": "male",
            "female": "female",
            "f": "female",
            "other": "other",
            "unknown": "unknown",
        }
        normalized = str(gender).strip().lower()
        if normalized in gender_map:
            patient["gender"] = gender_map[normalized]

    # Only include an identifier if the source contains a real value.
    health_card = first(
        "health_card_number", "health_card", "health_insurance_no"
    )
    if health_card:
        patient["identifier"] = [{
            "type": {"text": "Health card number"},
            "value": str(health_card),
        }]

    add(patient)

    # ---------------------------------------------------------
    # 2. Encounter
    # ---------------------------------------------------------

    encounter: dict[str, Any] = {
        "resourceType": "Encounter",
        "id": encounter_id,
        "status": "finished",
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "EMER",
            "display": "emergency",
        },
        "subject": reference("Patient", patient_id),
    }

    chief_complaint = first(
        "chief_complaint",
        "presenting_complaint",
        "complaint",
    )

    if chief_complaint:
        encounter["reasonCode"] = [concept(chief_complaint)]

    # Do not create Encounter.period using clock times alone.
    # FHIR dateTime/Period values require actual date information.
    add(encounter)

    # ---------------------------------------------------------
    # 3. Vital signs -> Observation resources
    # ---------------------------------------------------------

    # Preferred input:
    # "vital_signs": [
    #   {"name": "heart_rate", "value": 110, "unit": "/min",
    #    "time": "14:15"}
    # ]

    vital_codes = {
        "heart_rate": ("8867-4", "Heart rate", "/min"),
        "pulse": ("8867-4", "Heart rate", "/min"),
        "pulse_rate": ("8867-4", "Heart rate", "/min"),
        "respiratory_rate": ("9279-1", "Respiratory rate", "/min"),
        "resp_rate": ("9279-1", "Respiratory rate", "/min"),
        "temperature": ("8310-5", "Body temperature", "Cel"),
        "temp": ("8310-5", "Body temperature", "Cel"),
        "spo2": ("59408-5", "Oxygen saturation", "%"),
        "oxygen_saturation": ("59408-5", "Oxygen saturation", "%"),
        "weight": ("29463-7", "Body weight", "kg"),
    }

    procedures = deepcopy(acr.get("procedures", []))
    if not isinstance(procedures, list):
        procedures = []

    vital_signs = deepcopy(acr.get("vital_signs", []))
    if not isinstance(vital_signs, list):
        vital_signs = []

    for procedure in procedures:
        if not isinstance(procedure, dict):
            continue
        if str(procedure.get("procedure", "")).strip() not in ("010", "10"):
            continue
        for column, name, unit in (
            ("pulse_rate", "pulse_rate", "/min"),
            ("resp_rate", "resp_rate", "/min"),
            ("temp", "temp", "Cel"),
            ("spo2", "spo2", "%"),
        ):
            value = procedure.get(column)
            if present(value):
                vital_signs.append({
                    "name": name,
                    "value": value,
                    "unit": unit,
                    "time": procedure.get("time"),
                })

    # Also support common scalar ACR fields.
    for key in (
        "heart_rate",
        "pulse",
        "respiratory_rate",
        "temperature",
        "spo2",
        "oxygen_saturation",
        "weight",
        "weight_kg",
    ):
        value = acr.get(key)
        if present(value):
            vital_signs.append({
                "name": "weight" if key == "weight_kg" else key,
                "value": value,
            })

    # Blood pressure is represented as one Observation with components.
    blood_pressure = acr.get("blood_pressure", acr.get("bp"))
    if not present(blood_pressure):
        blood_pressure = next(
            (
                procedure.get("bp")
                for procedure in procedures
                if isinstance(procedure, dict)
                and str(procedure.get("procedure", "")).strip() in ("010", "10")
                and present(procedure.get("bp"))
            ),
            None,
        )

    if isinstance(blood_pressure, dict):
        systolic = parse_number(
            blood_pressure.get("systolic")
        )
        diastolic = parse_number(
            blood_pressure.get("diastolic")
        )

    elif isinstance(blood_pressure, str):
        match = re.fullmatch(
            r"\s*(\d{2,3})\s*/\s*(\d{2,3})\s*",
            blood_pressure,
        )
        if match:
            systolic = int(match.group(1))
            diastolic = int(match.group(2))
        else:
            systolic = diastolic = None
    else:
        systolic = parse_number(acr.get("systolic_bp"))
        diastolic = parse_number(acr.get("diastolic_bp"))

    if systolic is not None or diastolic is not None:
        components = []

        if systolic is not None:
            components.append({
                "code": {
                    "coding": [{
                        "system": "http://loinc.org",
                        "code": "8480-6",
                        "display": "Systolic blood pressure",
                    }]
                },
                "valueQuantity": {
                    "value": systolic,
                    "unit": "mmHg",
                    "system": "http://unitsofmeasure.org",
                    "code": "mm[Hg]",
                },
            })

        if diastolic is not None:
            components.append({
                "code": {
                    "coding": [{
                        "system": "http://loinc.org",
                        "code": "8462-4",
                        "display": "Diastolic blood pressure",
                    }]
                },
                "valueQuantity": {
                    "value": diastolic,
                    "unit": "mmHg",
                    "system": "http://unitsofmeasure.org",
                    "code": "mm[Hg]",
                },
            })

        add({
            "resourceType": "Observation",
            "id": "blood-pressure",
            "status": "final",
            "category": [{
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                    "code": "vital-signs",
                    "display": "Vital Signs",
                }]
            }],
            "code": {
                "coding": [{
                    "system": "http://loinc.org",
                    "code": "85354-9",
                    "display": "Blood pressure panel",
                }]
            },
            "subject": reference("Patient", patient_id),
            "encounter": reference("Encounter", encounter_id),
            "component": components,
        })

    for index, vital in enumerate(vital_signs):
        if not isinstance(vital, dict):
            continue

        name_key = str(vital.get("name", "")).lower()
        definition = vital_codes.get(name_key)
        value = parse_number(vital.get("value"))

        if not definition or value is None:
            continue

        code, display, default_unit = definition
        unit = vital.get("unit", default_unit)

        observation = {
            "resourceType": "Observation",
            "id": f"vital-{index + 1}",
            "status": "final",
            "category": [{
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                    "code": "vital-signs",
                    "display": "Vital Signs",
                }]
            }],
            "code": {
                "coding": [{
                    "system": "http://loinc.org",
                    "code": code,
                    "display": display,
                }]
            },
            "subject": reference("Patient", patient_id),
            "encounter": reference("Encounter", encounter_id),
            "valueQuantity": {
                "value": value,
                "unit": unit,
            },
        }

        if unit == default_unit:
            observation["valueQuantity"]["system"] = (
                "http://unitsofmeasure.org"
            )
            observation["valueQuantity"]["code"] = default_unit

        # A time-only value must not be converted into a fake dateTime.
        event_time = vital.get("time")
        if event_time:
            observation["note"] = [{
                "text": f"Source event time (time only): {event_time}"
            }]

        add(observation)

    # ---------------------------------------------------------
    # 4. Chief complaint / clinical problems -> Condition
    # ---------------------------------------------------------

    conditions = deepcopy(acr.get("conditions", []))
    if not isinstance(conditions, list):
        conditions = []

    history_labels = {
        "history_cardiac": "Cardiac history",
        "history_respiratory": "Respiratory history",
        "history_seizure": "Seizure history",
        "history_diabetes": "Diabetes history",
        "history_psychiatric": "Psychiatric history",
        "history_anaphylaxis": "Anaphylaxis history",
        "history_cancer": "Cancer history",
        "history_stroke_tia": "Stroke/TIA history",
        "history_hypertension": "Hypertension history",
        "history_previously_healthy": "Previously healthy",
    }
    conditions.extend(
        label for key, label in history_labels.items() if acr.get(key) is True
    )
    medical_history = first(
        "medical_history", "past_medical_history", "history_details"
    )
    if medical_history:
        history_items = (
            medical_history
            if isinstance(medical_history, list)
            else [medical_history]
        )
        for item in history_items:
            if present(item):
                conditions.append({
                    "description": str(item),
                    "kind": "history",
                })

    for index, item in enumerate(conditions):
        if isinstance(item, str):
            description = item
        elif isinstance(item, dict):
            description = (
                item.get("description")
                or item.get("name")
                or item.get("condition")
            )
        else:
            continue

        if not present(description):
            continue

        add({
            "resourceType": "Condition",
            "id": f"condition-{index + 1}",
            "subject": reference("Patient", patient_id),
            "encounter": reference("Encounter", encounter_id),
            "code": concept(description),
        })

    # ---------------------------------------------------------
    # 5. Allergies -> AllergyIntolerance
    # ---------------------------------------------------------

    allergies = deepcopy(acr.get("allergies"))
    if not isinstance(allergies, list):
        allergies = []
    allergy_details = first("allergies_details")
    if allergy_details and not acr.get("allergies_nka") and not acr.get("allergies_cno"):
        allergies.append(allergy_details)
    if isinstance(allergies, list):
        for index, allergy in enumerate(allergies):
            if isinstance(allergy, str):
                description = allergy
            elif isinstance(allergy, dict):
                description = (
                    allergy.get("substance")
                    or allergy.get("name")
                    or allergy.get("description")
                )
            else:
                continue

            if not present(description):
                continue

            add({
                "resourceType": "AllergyIntolerance",
                "id": f"allergy-{index + 1}",
                "patient": reference("Patient", patient_id),
                "code": concept(description),
            })

    # Do not infer "no known allergies" from a missing/empty field.
    # If your ACR explicitly records allergy status, map it separately.

    # ---------------------------------------------------------
    # 6. Administered medications -> MedicationAdministration
    # ---------------------------------------------------------

    medications = acr.get("medications", [])
    if not isinstance(medications, list):
        medications = []

    for index, medication in enumerate(medications):
        if not isinstance(medication, dict):
            continue

        name = (
            medication.get("name")
            or medication.get("medication")
        )
        if not present(name):
            continue

        resource = {
            "resourceType": "MedicationAdministration",
            "id": f"medication-admin-{index + 1}",
            "status": "completed",
            "medicationCodeableConcept": concept(name),
            "subject": reference("Patient", patient_id),
            "context": reference("Encounter", encounter_id),
        }

        dose = parse_number(medication.get("dose"))
        unit = medication.get("unit")

        if dose is not None and unit:
            resource["dosage"] = {
                "dose": {
                    "value": dose,
                    "unit": unit,
                }
            }

        route = medication.get("route")
        if route:
            resource.setdefault("dosage", {})["route"] = concept(route)

        administration_time = medication.get("time")
        if administration_time:
            # Preserve a time-only value as a note; do not invent a date.
            resource["note"] = [{
                "text": f"Source administration time (time only): {administration_time}"
            }]

        add(resource)

    # ---------------------------------------------------------
    # 7. Procedures -> Procedure
    # ---------------------------------------------------------

    for index, procedure in enumerate(procedures):
        if isinstance(procedure, str):
            description = procedure
            procedure_time = None
        elif isinstance(procedure, dict):
            description = (
                procedure.get("name")
                or procedure.get("procedure")
                or procedure.get("description")
            )
            procedure_time = procedure.get("time")
        else:
            continue

        if not present(description):
            continue

        resource = {
            "resourceType": "Procedure",
            "id": f"procedure-{index + 1}",
            "status": "completed",
            "code": concept(description),
            "subject": reference("Patient", patient_id),
            "encounter": reference("Encounter", encounter_id),
        }

        if procedure_time:
            resource["note"] = [{
                "text": f"Source procedure time (time only): {procedure_time}"
            }]

        add(resource)

    # ---------------------------------------------------------
    # 8. Return a FHIR Bundle
    # ---------------------------------------------------------

    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {"resource": resource}
            for resource in resources
        ],
    }