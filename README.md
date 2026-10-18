# Ambulance Call Report – home page + editable form

    pip install flask pydantic
    python app.py                 # http://127.0.0.1:5000/
    python build_static.py        # -> acr_form.html (blank form only, no server / home page / saving)

## Flow
* `/` **Home** – every report in `ems_encounters.json`, newest first. Search, filter (All / Unposted / Posted).
  Click a row to select it, then use the action bar: **Add report**, **Update report** (opens the form),
  **Remove**. `New Incident` (top) and the row's arrow button also open the form. Double-click opens a row.
* `/encounters/new` – blank form; the record is created in the JSON file on first **Save**.
* `/encounters/<id>` – the form prefilled from that encounter. **Save** (or Ctrl/Cmd+S) writes it back;
  **Sign & transmit** sets `paramedic_signed_off` (button becomes **Reopen**).

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
    prehospital_scribe.py  unchanged (imported)     reference_codes.py   only the Medications table is used now
    templates/home.html, acr.html                   static/home.css|js, encounter.css|js, acr.css|js

## API
    GET /api/encounters   POST /api/encounters   GET|PUT|DELETE /api/encounters/<id>   POST /api/encounters/<id>/sign

Form JS API (`ACR.*`) is unchanged, plus `ACR.getProcedures()`.
