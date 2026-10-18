# Ambulance Call Report – home page + editable form

    pip install flask pydantic ollama numpy faster-whisper sounddevice "av<19" python-dotenv
    # Optional test-only audio file import on incident pages:
    # Add TEST=True to .env, then restart the app.
    ollama serve                 # if the Ollama app/service is not already running
    ollama pull llama3:latest
    python app.py                 # http://127.0.0.1:5000/
    python build_static.py        # -> acr_form.html (blank form only, no server / home page / saving)

## Flow
* `/` **Home** – every report in `ems_encounters.json`, newest first. Search, filter (All / Unposted / Posted).
  Click a row to select it, then use the action bar: **Add report**, **Update report** (opens the form),
  **Record audio**, **Remove**. Recording is available for unposted incidents; allow microphone access,
  speak the update, then press **Stop recording**. The transcript is extracted into ACR fields and saved
  automatically. `New Incident` and the refresh button are at the top right; the refresh icon spins while
  the incident list reloads. The row's arrow button also opens the form. Double-click opens a row.
* `/encounters/new` – blank form; the record is created in the JSON file on first **Save**.
* `/encounters/<id>` – the form prefilled from that encounter. **Save** (or Ctrl/Cmd+S) writes it back;
  **Record audio** adds a voice update to the open incident; **Post** sets `paramedic_signed_off`
  (button becomes **Unpost**).

The web microphone is controlled by the browser, so the app must be served from localhost or another
secure context and microphone permission must be granted. Stop recording to automatically transcribe
with faster-whisper, extract sparse ACR form fields with local Ollama (`llama3:latest`), and merge
those fields into the selected incident without clearing existing form data. Audio is uploaded only to
this app, held temporarily for transcription, and then discarded. The standalone `python prehospital_scribe.py`
workflow remains interactive in the terminal. Ollama must be running and the `llama3:latest` model
must be available; if it is unavailable, the audio API returns HTTP 503 with a clear retry message.
Set `TEST=True` in `.env` and restart the app to show **Import audio file** on incident pages. It accepts
common browser-recording and audio file formats, including WebM, OGG, MP4/M4A, MP3, and WAV.

## ems_encounters.json
`{"encounters": [ ... ]}` (a bare top-level list is also accepted). Each encounter has every field of
`prehospital_scribe.PatientRecord` plus `incident_no`, `created_at`, `updated_at` and an `acr` block
(the flat form fields + a `procedures` list). On every save, `encounter_store.form_to_record()` re-derives the
record's chief complaint / onset / allergies / medications / vitals from the form and re-runs
`run_protocol_and_validation_engine`, so `missing_fields` and `protocol_triggers` stay current and are shown
above the form and on the home list. A record written by the scribe with no `acr` block yet is opened in the
form by mapping its fields back onto form fields (`record_to_form`). Once a form has been saved, the form is
the source of truth.

## Files
    app.py               routes + JSON API          encounter_store.py   JSON store + form<->PatientRecord mapping
    prehospital_scribe.py  audio/LLM extraction + interactive CLI
    reference_codes.py   medication code mapping used by the store
    templates/home.html, acr.html                   static/home.css|js, encounter.css|js, acr.css|js

## API
    GET /api/encounters   POST /api/encounters   GET|PUT|DELETE /api/encounters/<id>
    POST /api/encounters/<id>/sign   POST /api/encounters/<id>/audio

Form JS API (`ACR.*`) is unchanged, plus `ACR.getProcedures()`.
