import re
from html.parser import HTMLParser
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app
from prehospital_scribe import (
    ACR_FIELDS,
    ACR_SECTION_FIELDS,
    ACR_SCALAR_FIELDS,
    CHECKBOX_FIELDS,
    PROCEDURE_FIELDS,
    _build_extraction_prompt,
    _chat_with_acr_schema,
    extract_data_with_llm,
    _validate_extraction,
    build_acr_schema,
)


ROOT = Path(__file__).resolve().parents[1]


class FormFieldParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}
        self.radio_values = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        name = attributes.get("data-field")
        if not name:
            return
        field_type = (
            attributes.get("type")
            or attributes.get("data-type")
            or ("textarea" if tag == "textarea" else "text")
        )
        self.fields.setdefault(name, set()).add(field_type)
        if field_type == "radio":
            self.radio_values.setdefault(name, set()).add(attributes.get("value", ""))


class ScribeSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        response = app.app.test_client().get("/encounters/new")
        if response.status_code != 200:
            raise AssertionError(f"Blank ACR form failed to render: HTTP {response.status_code}")
        cls.form = FormFieldParser()
        cls.form.feed(response.get_data(as_text=True))
        cls.javascript = (ROOT / "static" / "acr.js").read_text(encoding="utf-8")

    def test_supported_acr_fields_match_form_and_types(self):
        rendered_names = {
            name for name in self.form.fields if not name.startswith("proc_")
        }
        self.assertTrue(ACR_FIELDS <= rendered_names, ACR_FIELDS - rendered_names)
        self.assertEqual(ACR_FIELDS, ACR_SCALAR_FIELDS | CHECKBOX_FIELDS)
        self.assertFalse(ACR_SCALAR_FIELDS & CHECKBOX_FIELDS)

        rendered_checkboxes = {
            name for name, types in self.form.fields.items()
            if "checkbox" in types
        } & ACR_FIELDS
        self.assertEqual(CHECKBOX_FIELDS, rendered_checkboxes)
        self.assertTrue(all(self.form.fields[name] == {"checkbox"} for name in CHECKBOX_FIELDS))
        self.assertEqual(
            self.form.radio_values["peripheral_edema"], {"absent", "present"}
        )
        self.assertEqual(
            self.form.radio_values["pedal_pulse"], {"absent", "present"}
        )

    def test_procedure_fields_match_html_and_javascript(self):
        javascript_columns = re.search(
            r"var PROC_COLUMNS = \[(.*?)\];", self.javascript, re.DOTALL
        )
        self.assertIsNotNone(javascript_columns)
        javascript_fields = set(re.findall(r"'([^']+)'", javascript_columns.group(1)))

        html_columns = {
            name.removeprefix("proc_1_")
            for name in self.form.fields
            if name.startswith("proc_1_")
        }

        self.assertEqual(PROCEDURE_FIELDS, javascript_fields)
        self.assertEqual(PROCEDURE_FIELDS, html_columns)

    def test_every_form_section_has_a_clear_control_for_its_content(self):
        html = app.app.test_client().get("/encounters/new").get_data(as_text=True)
        content_ids = re.findall(
            r'<div class="acr-toggle-content" id="([^"]+)">', html
        )
        clear_targets = re.findall(r'data-clear-section="([^"]+)"', html)

        self.assertEqual(clear_targets, content_ids)
        self.assertEqual(clear_targets.count("clinical-demographics-content"), 1)
        self.assertEqual(clear_targets.count("billing-demographics-content"), 1)

    def test_section_field_sets_cover_the_acr_schema(self):
        section_fields = set().union(*ACR_SECTION_FIELDS.values())
        self.assertEqual(section_fields, ACR_FIELDS | {"procedures"})

        for section, fields in ACR_SECTION_FIELDS.items():
            with self.subTest(section=section):
                schema_fields = set(build_acr_schema(fields)["properties"])
                self.assertEqual(schema_fields, fields)
                self.assertTrue(fields - {"procedures"} <= ACR_FIELDS)

    def test_encounter_form_renders_a_microphone_for_every_section(self):
        encounter = {"encounter_id": "test-id", "incident_no": "123"}
        with (
            patch.object(app, "get_or_404", return_value=encounter),
            patch.object(app, "merged_form", return_value={}),
        ):
            response = app.app.test_client().get("/encounters/test-id")

        self.assertEqual(response.status_code, 200)
        rendered_sections = re.findall(
            r'data-scribe-section="([^"]+)"',
            response.get_data(as_text=True),
        )
        self.assertEqual(set(rendered_sections), set(ACR_SECTION_FIELDS))
        self.assertEqual(rendered_sections.count("demographics"), 2)

    def test_section_validation_rejects_fields_from_other_sections(self):
        with self.assertRaisesRegex(ValueError, "unsupported ACR fields: age"):
            _validate_extraction(
                {"chief_complaint": "Chest pain", "age": "44"},
                fields=ACR_SECTION_FIELDS["clinical-information"],
            )

    def test_prompt_field_lists_are_derived_from_schema_sets(self):
        prompt = _build_extraction_prompt("schema probe")
        scalar_line = "Scalar form fields: " + ", ".join(sorted(ACR_SCALAR_FIELDS)) + "."
        checkbox_line = (
            "Checkbox fields: " + ", ".join(sorted(CHECKBOX_FIELDS))
            + ". Checkbox values must be JSON booleans (true or false)."
        )
        procedure_line = (
            "The procedures field is a list of rows using only these procedure columns:\n"
            + ", ".join(sorted(PROCEDURE_FIELDS))
            + "."
        )
        self.assertIn(scalar_line, prompt)
        self.assertIn(checkbox_line, prompt)
        self.assertIn(procedure_line, prompt)
        self.assertIn('use the exact radio choice "absent" or "present"', prompt)

    def test_ollama_schema_requires_all_supported_fields(self):
        schema = build_acr_schema()
        properties = schema["properties"]
        procedure_schema = properties["procedures"]
        procedure_item = procedure_schema["items"]

        self.assertEqual(schema["type"], "object")
        self.assertEqual(set(schema["required"]), set(properties))
        self.assertFalse(schema["additionalProperties"])
        for field in ACR_FIELDS:
            expected_type = "boolean" if field in CHECKBOX_FIELDS else "string"
            self.assertEqual(properties[field], {"type": expected_type})
        self.assertEqual(procedure_schema["type"], "array")
        self.assertEqual(procedure_item["type"], "object")
        self.assertEqual(set(procedure_item["required"]), PROCEDURE_FIELDS)
        self.assertEqual(
            set(procedure_item["properties"]), PROCEDURE_FIELDS
        )
        self.assertFalse(procedure_item["additionalProperties"])

    def test_ollama_request_uses_schema_and_deterministic_generation(self):
        chat = Mock()
        _chat_with_acr_schema(chat, "model:latest", "extract this")

        kwargs = chat.call_args.kwargs
        self.assertEqual(kwargs["model"], "model:latest")
        self.assertEqual(kwargs["messages"][1], {
            "role": "user",
            "content": "extract this",
        })
        self.assertEqual(kwargs["format"], build_acr_schema())
        self.assertEqual(
            kwargs["options"], {"temperature": 0, "num_predict": 2048}
        )
        self.assertEqual(kwargs["messages"][0]["role"], "system")

    def test_local_ollama_extraction_parses_schema_response(self):
        response = SimpleNamespace(
            message=SimpleNamespace(content='{"chief_complaint":"chest pain"}'),
            load_duration=0,
            prompt_eval_duration=0,
            eval_duration=0,
            prompt_eval_count=0,
            eval_count=0,
        )
        with patch("ollama.chat", return_value=response) as chat:
            extraction = extract_data_with_llm("Patient reports chest pain.")

        self.assertEqual(extraction, {"chief_complaint": "chest pain"})
        self.assertEqual(chat.call_args.kwargs["format"], build_acr_schema())

    def test_extraction_validation_normalizes_scalar_and_procedure_values(self):
        extraction = _validate_extraction({
            "age": 65,
            "allergies_nka": False,
            "procedures": [{"procedure": 10, "reading": 14.2}],
        })

        self.assertEqual(extraction, {
            "age": "65",
            "allergies_nka": False,
            "procedures": [{"procedure": "10", "reading": "14.2"}],
        })

    def test_legacy_call_time_maps_to_call_received_form_field(self):
        self.assertEqual(
            _validate_extraction({"call_time": "14:40:00"}),
            {"ev_call_received": "14:40:00"},
        )
        self.assertEqual(
            _validate_extraction({
                "call_time": "14:40:00",
                "ev_call_received": "14:41:00",
            }),
            {"ev_call_received": "14:41:00"},
        )


if __name__ == "__main__":
    unittest.main()
