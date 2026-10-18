from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import app


class ScribeTextInputTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        self.store_patch = patch.object(app, "store")
        self.store = self.store_patch.start()
        self.store.get.return_value = {"paramedic_signed_off": False}
        self.store.apply_scribe_update.return_value = {"encounter_id": "test-id"}
        self.detail_patch = patch.object(app, "detail", return_value={})
        self.detail_patch.start()
        self.form_patch = patch.object(app, "merged_form", return_value={})
        self.form_patch.start()
        self.addCleanup(self.form_patch.stop)
        self.addCleanup(self.detail_patch.stop)
        self.addCleanup(self.store_patch.stop)

    def test_test_mode_transcript_goes_directly_to_llm(self):
        with (
            patch.dict(app.app.config, {"TEST_AUDIO_IMPORT": True}),
            patch.object(app, "REMOTE_MODEL_CONFIG", None),
            patch.dict("sys.modules", {"modules.stt": None}),
            patch("prehospital_scribe.extract_data_with_llm",
                  return_value={"chief_complaint": "chest pain"}) as extract,
        ):
            response = self.client.post(
                "/api/encounters/test-id/audio",
                data={"source": "text", "transcript": "Patient reports chest pain."},
            )

        self.assertEqual(response.status_code, 200)
        extract.assert_called_once_with("Patient reports chest pain.")
        self.store.apply_scribe_update.assert_called_once_with(
            "test-id", {"chief_complaint": "chest pain"}
        )

    def test_text_entry_is_rendered_only_in_test_mode(self):
        with patch.dict(app.app.config, {"TEST_AUDIO_IMPORT": True}):
            test_mode_page = self.client.get("/encounters/new").get_data(as_text=True)
        with patch.dict(app.app.config, {"TEST_AUDIO_IMPORT": False}):
            normal_page = self.client.get("/encounters/new").get_data(as_text=True)

        self.assertIn('id="scribe-text"', test_mode_page)
        self.assertIn("Upload audio", test_mode_page)
        self.assertNotIn('id="scribe-text"', normal_page)
        self.assertNotIn("Upload audio", normal_page)

    def test_transcript_input_is_rejected_when_test_mode_is_disabled(self):
        with (
            patch.dict(app.app.config, {"TEST_AUDIO_IMPORT": False}),
            patch("prehospital_scribe.extract_data_with_llm") as extract,
        ):
            response = self.client.post(
                "/api/encounters/test-id/audio",
                data={"source": "text", "transcript": "Patient reports chest pain."},
            )

        self.assertEqual(response.status_code, 403)
        extract.assert_not_called()

    def test_remote_mode_sends_transcript_to_remote_ollama(self):
        remote_config = SimpleNamespace(request_timeout=42)
        with (
            patch.dict(app.app.config, {"TEST_AUDIO_IMPORT": True}),
            patch.object(app, "REMOTE_MODEL_CONFIG", remote_config),
            patch.object(app, "remote_ollama_tunnel",
                         return_value=nullcontext("http://127.0.0.1:11434")),
            patch("prehospital_scribe.extract_data_with_llm",
                  return_value={"chief_complaint": "chest pain"}) as extract,
        ):
            response = self.client.post(
                "/api/encounters/test-id/audio",
                data={"source": "text", "transcript": "Patient reports chest pain."},
            )

        self.assertEqual(response.status_code, 200)
        extract.assert_called_once_with(
            "Patient reports chest pain.",
            ollama_host="http://127.0.0.1:11434",
            ollama_timeout=42,
        )

    def test_empty_procedures_do_not_clear_saved_procedures(self):
        with (
            patch.dict(app.app.config, {"TEST_AUDIO_IMPORT": True}),
            patch.object(app, "REMOTE_MODEL_CONFIG", None),
            patch(
                "prehospital_scribe.extract_data_with_llm",
                return_value={
                    "chief_complaint": "chest pain",
                    "procedures": [],
                },
            ),
        ):
            response = self.client.post(
                "/api/encounters/test-id/audio",
                data={"source": "text", "transcript": "Patient reports chest pain."},
            )

        self.assertEqual(response.status_code, 200)
        self.store.apply_scribe_update.assert_called_once_with(
            "test-id", {"chief_complaint": "chest pain"}
        )


if __name__ == "__main__":
    unittest.main()
