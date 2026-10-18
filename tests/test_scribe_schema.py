import re
from html.parser import HTMLParser
from pathlib import Path
import unittest

import app
from prehospital_scribe import (
    ACR_FIELDS,
    ACR_SCALAR_FIELDS,
    CHECKBOX_FIELDS,
    PROCEDURE_FIELDS,
    _build_extraction_prompt,
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

    def test_legacy_call_time_maps_to_call_received_form_field(self):
        from prehospital_scribe import _validate_extraction

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
