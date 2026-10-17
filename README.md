# Ambulance Call Report – editable HTML form

    pip install flask
    python app.py                 # http://127.0.0.1:5000/   (/demo shows server prefill)
    python build_static.py        # -> acr_form.html, one self-contained file (no server needed)

Every field has `data-field="<name>"`; `ACR.schema()` in the browser console lists them all
(name, type, label).  Duplicate fields (demographics on several pages) stay in sync.

JS API: `ACR.set(name, v)`, `ACR.get(name)`, `ACR.populate({...})`, `ACR.collect()`, `ACR.clear()`,
`ACR.focus(name)`, `ACR.onChange(fn)`, `ACR.setProcedures([...])`, `ACR.addProcedureRow()`,
`ACR.load(url)`, `ACR.save(url)`.   Values: text = string, checkbox = true/false,
radio group = option value, date = "YYYY-MM-DD", time = "HH:MM[:SS]".

Header colours: CSS variables at the top of `static/acr.css`.
