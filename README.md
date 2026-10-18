# Ambulance Call Report – home page + editable form

    pip install flask python-dotenv pydantic ollama numpy faster-whisper sounddevice requests
    # Optional: set TEST=True in .env to show audio upload and direct transcript input.
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
secure context and microphone permission must be granted. While recording on an incident page, the
app periodically transcribes the accumulated audio with faster-whisper and updates the transcript
sidebar, including its wording highlights. Stop recording to run the existing final transcription,
extract sparse ACR form fields with Ollama (`llama3:latest`), and merge those fields into the selected
incident without clearing existing form data. Audio is uploaded only to this app, held temporarily
for transcription, and then discarded. The standalone `python prehospital_scribe.py` workflow remains
interactive in the terminal and uses local Ollama.
With `TEST=True` in `.env`, incident pages also show **Upload audio** and a text box for sending a
typed or pasted transcript directly to the scribe. A collapsible **Transcript** sidebar displays
and lets you edit the recognized text, and highlights a few common recognition artifacts and
repeated words for review. Test-mode recordings and uploaded audio stop after transcription until
you press **Send transcript to scribe**; text input skips Whisper and goes straight to Ollama.
Outside test mode, microphone audio is sent to Ollama automatically after transcription. Restart
the app after changing `.env`.

Set `REMOTE=True` in `.env` to route Ollama requests through an SSH tunnel. When both `REMOTE=True`
and `TEST=True`, audio selected with **Upload audio** is transcribed by the Vultr VM's Faster-Whisper
API through an SSH tunnel. Live microphone recordings continue to use local Whisper. For example:

```dotenv
REMOTE=True
REMOTE_SSH_HOST=your-user@your-vultr-host
# REMOTE_SSH_KEY=C:\Users\you\.ssh\id_ed25519
```

Configure `REMOTE_SSH_HOST` as `user@host` (and optionally `REMOTE_SSH_PORT`, `REMOTE_SSH_KEY`);
the VM's Faster-Whisper API must listen on port 8000 and provide `POST /transcribe` with a multipart
`file` field and a JSON response containing `text`. Override its VM-side listen address with
`REMOTE_WHISPER_HOST` if needed (default `127.0.0.1:8000`). Remote Ollama must be listening on the
VM at `REMOTE_OLLAMA_HOST` (default `127.0.0.1:11434`) with `llama3:latest` pulled. The remote
model request timeout defaults to 600 seconds (`REMOTE_MODEL_TIMEOUT`). Before starting the app,
connect once with `ssh user@host` to verify and add the VM's host key; app requests use noninteractive
SSH authentication. Leave `REMOTE` unset or false to use the existing local Whisper and Ollama models.
SSH keys should be configured through your local SSH agent or `REMOTE_SSH_KEY`; do not put private key
contents in `.env`.

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
