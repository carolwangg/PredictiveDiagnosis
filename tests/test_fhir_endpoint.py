import unittest
from unittest.mock import patch

import app
from acr_to_fhir import acr_to_fhir


class FhirEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        self.store_patch = patch.object(app, "store")
        self.store = self.store_patch.start()
        self.addCleanup(self.store_patch.stop)

    def test_encounter_page_returns_fhir_bundle_for_saved_acr(self):
        self.store.get.return_value = {
            "encounter_id": "test-id",
            "incident_no": 123,
            "acr": {
                "first_name": "Alex",
                "last_name": "Smith",
                "dob": "1990-03-04",
                "sex": "F",
                "health_insurance_no": "1234567890",
                "chief_complaint": "Chest pain",
                "history_cardiac": True,
                "allergies_details": "Penicillin",
                "procedures": [{
                    "procedure": "010",
                    "time": "14:25",
                    "pulse_rate": "88",
                    "bp": "120/80",
                    "spo2": "97",
                }],
            },
        }

        response = self.client.get("/encounters/test-id/fhir")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/fhir+json")
        bundle = response.get_json()
        self.assertEqual(bundle["resourceType"], "Bundle")
        resources = [entry["resource"] for entry in bundle["entry"]]
        patient = next(item for item in resources if item["resourceType"] == "Patient")
        encounter = next(item for item in resources if item["resourceType"] == "Encounter")
        self.assertEqual(patient["name"][0]["text"], "Alex Smith")
        self.assertEqual(patient["birthDate"], "1990-03-04")
        self.assertEqual(patient["gender"], "female")
        self.assertEqual(patient["identifier"][0]["value"], "1234567890")
        self.assertEqual(encounter["id"], "test-id")
        self.assertEqual(encounter["reasonCode"][0]["text"], "Chest pain")
        self.assertTrue(any(item["resourceType"] == "Condition" for item in resources))
        self.assertTrue(any(item["resourceType"] == "AllergyIntolerance" for item in resources))
        self.assertTrue(any(
            item["resourceType"] == "Observation"
            and item["id"] == "blood-pressure"
            for item in resources
        ))
        self.assertTrue(any(
            item["resourceType"] == "Observation"
            and item.get("code", {}).get("coding", [{}])[0].get("display") == "Heart rate"
            and item.get("valueQuantity", {}).get("value") == 88
            for item in resources
        ))

    def test_missing_encounter_returns_not_found(self):
        self.store.get.return_value = None

        response = self.client.get("/encounters/missing-id/fhir")

        self.assertEqual(response.status_code, 404)

    def test_converter_does_not_mutate_input_procedure_or_condition_lists(self):
        acr = {
            "conditions": [],
            "procedures": [{
                "procedure": "010",
                "pulse_rate": "88",
                "time": "14:25",
            }],
        }

        acr_to_fhir(acr)

        self.assertEqual(acr["conditions"], [])
        self.assertEqual(len(acr["procedures"]), 1)


if __name__ == "__main__":
    unittest.main()
