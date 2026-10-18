from contextlib import nullcontext
from io import BytesIO
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

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

    def test_home_shows_audio_command_button_before_new_incident(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="btn-voice-command"', html)
        self.assertIn(">Record audio</span>", html)
        self.assertLess(
            html.index('id="btn-voice-command"'),
            html.index('id="btn-new"'),
        )

    def test_home_voice_command_redirects_for_new_incident(self):
        with patch.object(app, "_transcribe_audio_upload", return_value="New incident.") as transcribe:
            response = self.client.post(
                "/api/voice-command",
                data={"audio": (BytesIO(b"audio bytes"), "command.wav")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {
            "transcript": "New incident.",
            "redirect_url": "/encounters/new",
        })
        transcribe.assert_called_once()

    def test_home_voice_command_opens_matching_incident_number(self):
        self.store.list.return_value = [
            {"incident_no": 123456, "encounter_id": "matching-id"},
            {"incident_no": 123457, "encounter_id": "other-id"},
        ]
        with patch.object(
            app, "_transcribe_audio_upload", return_value="Edit incident number 123,456."
        ):
            response = self.client.post(
                "/api/voice-command",
                data={"audio": (BytesIO(b"audio bytes"), "command.wav")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {
            "transcript": "Edit incident number 123,456.",
            "redirect_url": "/encounters/matching-id",
        })

    def test_home_voice_command_silently_ignores_unknown_incident_or_phrase(self):
        self.store.list.return_value = [
            {"incident_no": 123456, "encounter_id": "matching-id"},
        ]
        with patch.object(app, "_transcribe_audio_upload", side_effect=[
            "Edit incident 999999",
            "Please start a new incident",
        ]):
            missing = self.client.post(
                "/api/voice-command",
                data={"audio": (BytesIO(b"audio bytes"), "command.wav")},
                content_type="multipart/form-data",
            )
            unmatched = self.client.post(
                "/api/voice-command",
                data={"audio": (BytesIO(b"audio bytes"), "command.wav")},
                content_type="multipart/form-data",
            )

        self.assertEqual(missing.status_code, 200)
        self.assertIsNone(missing.json["redirect_url"])
        self.assertEqual(unmatched.status_code, 200)
        self.assertIsNone(unmatched.json["redirect_url"])

    def test_home_voice_command_posts_matching_unposted_incident(self):
        self.store.list.return_value = [
            {
                "incident_no": 123456,
                "encounter_id": "matching-id",
                "paramedic_signed_off": False,
            },
        ]
        updated = {
            "encounter_id": "matching-id",
            "incident_no": 123456,
            "paramedic_signed_off": True,
        }
        self.store.set_signed.return_value = updated
        with (
            patch.object(
                app, "_transcribe_audio_upload", return_value="Send incident 123,456."
            ),
            patch.object(app, "detail", return_value={
                "id": "matching-id",
                "incident_no": 123456,
                "signed": True,
            }),
        ):
            response = self.client.post(
                "/api/voice-command",
                data={"audio": (BytesIO(b"audio bytes"), "command.wav")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["posted"])
        self.assertEqual(response.json["incident_no"], 123456)
        self.store.set_signed.assert_called_once_with("matching-id", True)

    def test_home_voice_command_does_not_resign_or_change_missing_incident(self):
        self.store.list.return_value = [
            {
                "incident_no": 123456,
                "encounter_id": "matching-id",
                "paramedic_signed_off": True,
            },
        ]
        with patch.object(
            app, "_transcribe_audio_upload", return_value="Send incident 123456"
        ):
            already_posted = self.client.post(
                "/api/voice-command",
                data={"audio": (BytesIO(b"audio bytes"), "command.wav")},
                content_type="multipart/form-data",
            )

        self.assertEqual(already_posted.status_code, 200)
        self.assertTrue(already_posted.json["already_posted"])
        self.store.set_signed.assert_not_called()

        self.store.list.return_value = []
        with patch.object(
            app, "_transcribe_audio_upload", return_value="Send incident 999999"
        ):
            missing = self.client.post(
                "/api/voice-command",
                data={"audio": (BytesIO(b"audio bytes"), "command.wav")},
                content_type="multipart/form-data",
            )

        self.assertEqual(missing.status_code, 200)
        self.assertFalse(missing.json["posted"])
        self.store.set_signed.assert_not_called()

    def test_test_mode_transcript_goes_directly_to_llm(self):
        with (
            patch.dict(app.app.config, {"TEST_AUDIO_IMPORT": True}),
            patch.object(app, "REMOTE_MODEL_CONFIG", None),
            patch.dict("sys.modules", {"modules.stt": None}),
            patch("prehospital_scribe.extract_data_with_llm",
                  return_value={"chief_complaint": "chest pain"}) as extract,
        ):
            response = self.client.post(
                "/api/encounters/test-id/text",
                json={"transcript": "Patient reports chest pain."},
            )

        self.assertEqual(response.status_code, 200)
        extract.assert_called_once_with("Patient reports chest pain.")
        self.store.apply_scribe_update.assert_called_once_with(
            "test-id", {"chief_complaint": "chest pain"}
        )

    def test_section_scoped_text_is_forwarded_to_the_extractor(self):
        with (
            patch.dict(app.app.config, {"TEST_AUDIO_IMPORT": True}),
            patch.object(app, "REMOTE_MODEL_CONFIG", None),
            patch(
                "prehospital_scribe.extract_data_with_llm",
                return_value={"chief_complaint": "chest pain"},
            ) as extract,
        ):
            response = self.client.post(
                "/api/encounters/test-id/text",
                json={
                    "transcript": "Patient reports chest pain.",
                    "section": "clinical-information",
                },
            )

        self.assertEqual(response.status_code, 200)
        extract.assert_called_once_with(
            "Patient reports chest pain.", section="clinical-information"
        )

    def test_manual_send_and_audio_upload_are_test_mode_only(self):
        with patch.dict(app.app.config, {"TEST_AUDIO_IMPORT": True}):
            test_mode_page = self.client.get("/encounters/new").get_data(as_text=True)
        with patch.dict(app.app.config, {"TEST_AUDIO_IMPORT": False}):
            normal_page = self.client.get("/encounters/new").get_data(as_text=True)

        self.assertIn('id="scribe-text"', test_mode_page)
        self.assertIn('id="btn-transcript"', test_mode_page)
        self.assertIn('id="transcript-sidebar"', test_mode_page)
        self.assertIn('id="transcript-review"', test_mode_page)
        for section_id in (
            "clinical-demographics-content",
            "clinical-info-content",
            "cardiac-arrest-content",
            "physical-exam-content",
            "billing-info-content",
            "billing-demographics-content",
            "clinical-procedures-content",
            "crew-content",
            "general-administration-content",
            "call-events-content",
            "remarks-content",
        ):
            self.assertIn('aria-controls="' + section_id + '"', test_mode_page)
        self.assertIn("Upload audio", test_mode_page)
        self.assertEqual(
            set(re.findall(r'data-scribe-upload="([^"]+)"', test_mode_page)),
            {
                "demographics",
                "clinical-information",
                "cardiac-arrest-information",
                "physical-exam",
                "billing-information",
                "clinical-treatment-procedures",
                "crew",
                "general-administration",
                "call-events",
                "remarks-disposition",
            },
        )
        self.assertIn('id="btn-scribe-text"', test_mode_page)
        self.assertIn('id="scribe-text"', normal_page)
        self.assertIn('id="btn-transcript"', normal_page)
        self.assertIn('id="transcript-sidebar"', normal_page)
        self.assertIn('id="transcript-review"', normal_page)
        self.assertNotIn('id="btn-scribe-text"', normal_page)
        self.assertNotIn("Upload audio", normal_page)
        self.assertNotIn("data-scribe-upload", normal_page)
        self.assertNotIn('id="btn-save"', test_mode_page)
        self.assertNotIn('id="btn-save"', normal_page)

    def test_transcript_input_is_rejected_when_test_mode_is_disabled(self):
        with (
            patch.dict(app.app.config, {"TEST_AUDIO_IMPORT": False}),
            patch("prehospital_scribe.extract_data_with_llm") as extract,
        ):
            response = self.client.post(
                "/api/encounters/test-id/text",
                json={"transcript": "Patient reports chest pain."},
            )

        self.assertEqual(response.status_code, 403)
        extract.assert_not_called()

    def test_empty_transcript_is_rejected(self):
        with (
            patch.dict(app.app.config, {"TEST_AUDIO_IMPORT": True}),
            patch("prehospital_scribe.extract_data_with_llm") as extract,
        ):
            response = self.client.post(
                "/api/encounters/test-id/text",
                json={"transcript": "   "},
            )

        self.assertEqual(response.status_code, 400)
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
                "/api/encounters/test-id/text",
                json={"transcript": "Patient reports chest pain."},
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
                "/api/encounters/test-id/text",
                json={"transcript": "Patient reports chest pain."},
            )

        self.assertEqual(response.status_code, 200)
        self.store.apply_scribe_update.assert_called_once_with(
            "test-id", {"chief_complaint": "chest pain"}
        )

    def test_test_mode_audio_returns_transcript_without_calling_llm(self):
        transcribe = Mock(return_value="Patient reports chest pain.")
        stt_module = SimpleNamespace(transcribe_audio_file=transcribe)
        with (
            patch.dict(app.app.config, {"TEST_AUDIO_IMPORT": True}),
            patch.object(app, "REMOTE_MODEL_CONFIG", None),
            patch.dict("sys.modules", {"modules.stt": stt_module}),
            patch("prehospital_scribe.extract_data_with_llm") as extract,
        ):
            response = self.client.post(
                "/api/encounters/test-id/audio",
                data={"audio": (BytesIO(b"audio bytes"), "recording.wav")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {
            "transcript": "Patient reports chest pain.",
            "awaiting_review": True,
        })
        transcribe.assert_called_once()
        extract.assert_not_called()

    def test_main_audio_voice_command_posts_current_incident_without_scribe(self):
        with (
            patch.dict(app.app.config, {"TEST_AUDIO_IMPORT": True}),
            patch.object(app, "_transcribe_audio_upload", return_value="Send incident."),
            patch.object(app, "detail", return_value={
                "id": "test-id",
                "incident_no": 123,
                "signed": True,
            }),
            patch("prehospital_scribe.extract_data_with_llm") as extract,
        ):
            response = self.client.post(
                "/api/encounters/test-id/audio",
                data={"audio": (BytesIO(b"audio bytes"), "recording.wav")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["command"], "post-incident")
        self.assertEqual(response.json["transcript"], "Send incident.")
        self.store.set_signed.assert_called_once_with("test-id", True)
        extract.assert_not_called()

    def test_section_audio_does_not_treat_send_incident_as_post_command(self):
        transcribe = Mock(return_value="Send incident.")
        stt_module = SimpleNamespace(transcribe_audio_file=transcribe)
        with (
            patch.dict(app.app.config, {"TEST_AUDIO_IMPORT": True}),
            patch.object(app, "REMOTE_MODEL_CONFIG", None),
            patch.dict("sys.modules", {"modules.stt": stt_module}),
            patch("prehospital_scribe.extract_data_with_llm") as extract,
        ):
            response = self.client.post(
                "/api/encounters/test-id/audio",
                data={
                    "audio": (BytesIO(b"audio bytes"), "recording.wav"),
                    "section": "clinical-information",
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["awaiting_review"])
        self.store.set_signed.assert_not_called()
        extract.assert_not_called()

    def test_test_mode_section_audio_preserves_scope_for_review(self):
        transcribe = Mock(return_value="Patient reports chest pain.")
        stt_module = SimpleNamespace(transcribe_audio_file=transcribe)
        with (
            patch.dict(app.app.config, {"TEST_AUDIO_IMPORT": True}),
            patch.object(app, "REMOTE_MODEL_CONFIG", None),
            patch.dict("sys.modules", {"modules.stt": stt_module}),
            patch("prehospital_scribe.extract_data_with_llm") as extract,
        ):
            response = self.client.post(
                "/api/encounters/test-id/audio",
                data={
                    "audio": (BytesIO(b"audio bytes"), "recording.wav"),
                    "source": "file",
                    "section": "clinical-information",
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {
            "transcript": "Patient reports chest pain.",
            "awaiting_review": True,
            "section": "clinical-information",
        })
        extract.assert_not_called()

    def test_invalid_audio_section_is_rejected(self):
        with (
            patch.dict(app.app.config, {"TEST_AUDIO_IMPORT": True}),
            patch.object(app, "_transcribe_audio_upload") as transcribe,
        ):
            response = self.client.post(
                "/api/encounters/test-id/audio",
                data={
                    "audio": (BytesIO(b"audio bytes"), "recording.wav"),
                    "section": "unknown-section",
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 400)
        transcribe.assert_not_called()

    def test_live_transcription_endpoint_returns_text_without_calling_llm(self):
        transcribe = Mock(return_value="Patient reports chest pain.")
        stt_module = SimpleNamespace(transcribe_audio_file=transcribe)
        with (
            patch.dict("sys.modules", {"modules.stt": stt_module}),
            patch("prehospital_scribe.extract_data_with_llm") as extract,
        ):
            response = self.client.post(
                "/api/encounters/test-id/transcribe",
                data={"audio": (BytesIO(b"audio bytes"), "live-recording.webm")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"transcript": "Patient reports chest pain."})
        transcribe.assert_called_once()
        extract.assert_not_called()

    def test_live_transcription_rejects_posted_incidents(self):
        self.store.get.return_value = {"paramedic_signed_off": True}
        with patch.object(app, "_transcribe_audio_upload") as transcribe:
            response = self.client.post(
                "/api/encounters/test-id/transcribe",
                data={"audio": (BytesIO(b"audio bytes"), "live-recording.webm")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 409)
        transcribe.assert_not_called()

    def test_non_test_audio_is_transcribed_and_sent_to_llm_automatically(self):
        transcript = "Patient reports chest pain."
        transcribe = Mock(return_value=transcript)
        stt_module = SimpleNamespace(transcribe_audio_file=transcribe)
        with (
            patch.dict(app.app.config, {"TEST_AUDIO_IMPORT": False}),
            patch.object(app, "REMOTE_MODEL_CONFIG", None),
            patch.dict("sys.modules", {"modules.stt": stt_module}),
            patch("prehospital_scribe.extract_data_with_llm",
                  return_value={"chief_complaint": "chest pain"}) as extract,
        ):
            response = self.client.post(
                "/api/encounters/test-id/audio",
                data={"audio": (BytesIO(b"audio bytes"), "recording.wav")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["transcript"], transcript)
        transcribe.assert_called_once()
        extract.assert_called_once_with(transcript)


if __name__ == "__main__":
    unittest.main()
