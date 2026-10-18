/*
 * Form page <-> ems_encounters.json.
 * Uses the ACR field API; the server (encounter_store.py) does the persistence.
 */
(function () {
  'use strict';
  var dataEl = document.getElementById('enc-data');
  if (!dataEl || !window.ACR) return;

  var meta = JSON.parse(dataEl.textContent);   // {id, incident_no, signed, api, home, page, ...}
  var dirty = false, busy = false;
  var $ = function (id) { return document.getElementById(id); };
  var stateEl = $('save-state'), saveBtn = $('btn-save'), signBtn = $('btn-sign'), pill = $('status-pill');
  var importButton = $('btn-import-audio');

  function setState(text, cls) { stateEl.textContent = text; stateEl.className = 'save-state ' + (cls || ''); }

  function payload() {
    var d = ACR.collect({ skipEmpty: true });
    Object.keys(d).forEach(function (k) { if (/^proc_\d+_/.test(k)) delete d[k]; });  // rows go in "procedures"
    d.procedures = ACR.getProcedures();
    return d;
  }

  function api(method, url, body) {
    return fetch(url, {
      method: method,
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
        return j;
      });
    });
  }

  /* ---------- alerts from the scribe rule engine ---------- */
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  function renderAlerts() {
    var box = $('alerts'), html = '';
    var miss = meta.missing_fields || [], prot = meta.protocol_triggers || [];
    if (miss.length) {
      html += '<h4>Needs attention</h4><ul>' + miss.map(function (m) { return '<li class="warn">' + esc(m) + '</li>'; }).join('') + '</ul>';
    }
    if (prot.length) {
      html += '<h4>Active protocols</h4><ul>' + prot.map(function (p) {
        return '<li class="proto"><b>' + esc(p.protocol) + '</b> <small>' + esc(p.status || '') + ' – ' +
          esc((p.required_actions || []).join(' · ')) + '</small></li>';
      }).join('') + '</ul>';
    }
    box.innerHTML = html;
    box.hidden = !html;
  }

  function applyResult(r) {
    meta.id = r.id; meta.incident_no = r.incident_no; meta.signed = r.signed;
    meta.missing_fields = r.missing_fields; meta.protocol_triggers = r.protocol_triggers;
    $('inc-no').textContent = r.incident_no;
    document.title = 'Incident ' + r.incident_no + ' – Ambulance Call Report';
    $('btn-record').disabled = !!r.signed;
    $('btn-record').title = r.signed ? 'Unpost this report before recording an update' : 'Record an update for this incident';
    if (importButton) importButton.disabled = !!r.signed || busy;
    pill.textContent = r.signed ? 'Posted' : 'Unposted';
    pill.dataset.signed = r.signed ? 'true' : 'false';
    signBtn.textContent = r.signed ? 'Unpost' : 'Post';
    signBtn.setAttribute('aria-label', r.signed ? 'Unpost incident' : 'Post incident');
    signBtn.classList.toggle('primary', !r.signed);
    renderAlerts();
  }

  /* ---------- save / sign ---------- */
  function save() {
    if (busy) return Promise.reject(new Error('busy'));
    busy = true; saveBtn.disabled = true; setState('Saving…');
    var creating = !meta.id;
    return api(creating ? 'POST' : 'PUT', creating ? meta.api : meta.api + '/' + meta.id, payload())
      .then(function (r) {
        if (creating) history.replaceState(null, '', meta.page + '/' + r.id);
        applyResult(r);
        dirty = false;
        setState('Saved ' + new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
        return r;
      })
      .catch(function (e) { setState('Save failed – ' + e.message, 'err'); throw e; })
      .then(function (r) { busy = false; saveBtn.disabled = false; return r; },
            function (e) { busy = false; saveBtn.disabled = false; throw e; });
  }

  function toggleSign() {
    var target = !meta.signed;
    var go = dirty || !meta.id ? save() : Promise.resolve();
    go.then(function () {
      if (target && meta.missing_fields.length &&
          !confirm('This report still has open items:\n\n• ' + meta.missing_fields.join('\n• ') +
                   '\n\nPost this incident anyway?')) return null;
      return api('POST', meta.api + '/' + meta.id + '/sign', { signed: target }).then(function (r) {
        applyResult(r); setState(target ? 'Incident posted' : 'Post removed');
      });
    }).catch(function (e) { if (e.message !== 'busy') setState('Failed – ' + e.message, 'err'); });
  }

  saveBtn.addEventListener('click', function () { save().catch(function () {}); });
  signBtn.addEventListener('click', toggleSign);

  ACRAudio.attach($('btn-record'), {
    isDisabled: function () { return !!meta.signed; },
    url: function () { return meta.api + '/' + encodeURIComponent(meta.id) + '/audio'; },
    beforeStart: function () {
      if (meta.signed) return Promise.reject(new Error('Unpost this report before recording an update.'));
      return dirty || !meta.id ? save() : Promise.resolve();
    },
    onResult: function (result) {
      ACR.clear();
      ACR.populate(result.form || {});
      applyResult(result);
      dirty = false;
      setState('Voice update saved');
    },
    onError: function (error) { setState('Voice update failed – ' + error.message, 'err'); }
  });

  if (importButton) {
    var audioFile = $('audio-file');
    importButton.addEventListener('click', function () { audioFile.click(); });
    audioFile.addEventListener('change', function () {
      var file = audioFile.files && audioFile.files[0];
      audioFile.value = '';
      if (!file) return;
      if (meta.signed) {
        setState('Unpost this report before importing audio.', 'err');
        return;
      }
      var recordingState = $('btn-record').dataset.state;
      if (recordingState && recordingState !== 'idle') {
        setState('Stop recording before importing an audio file.', 'err');
        return;
      }
      if (busy) return;

      importButton.disabled = true;
      $('btn-record').disabled = true;
      var saved = dirty || !meta.id ? save() : Promise.resolve();
      saved.then(function () {
        busy = true;
        saveBtn.disabled = true;
        signBtn.disabled = true;
        setState('Processing uploaded audio…');
        var body = new FormData();
        body.append('audio', file, file.name);
        return fetch(meta.api + '/' + encodeURIComponent(meta.id) + '/audio', {
          method: 'POST',
          headers: { Accept: 'application/json' },
          body: body
        }).then(function (response) {
          return response.json().catch(function () { return {}; }).then(function (result) {
            if (!response.ok) throw new Error(result.error || ('HTTP ' + response.status));
            ACR.clear();
            ACR.populate(result.form || {});
            applyResult(result);
            dirty = false;
            setState('Uploaded audio processed');
          });
        });
      }).catch(function (error) {
        setState('Audio upload failed – ' + error.message, 'err');
      }).then(function () {
        busy = false;
        importButton.disabled = !!meta.signed;
        saveBtn.disabled = false;
        signBtn.disabled = false;
        $('btn-record').disabled = !!meta.signed;
      });
    });
  }

  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save().catch(function () {}); }
  });
  ACR.onChange(function () { if (!dirty) { dirty = true; setState('Unsaved changes', 'dirty'); } });
  window.addEventListener('beforeunload', function (e) { if (dirty) { e.preventDefault(); e.returnValue = ''; } });

  // intercept the "All reports" link so unsaved edits aren't lost silently
  document.querySelector('#appbar .back').addEventListener('click', function (e) {
    if (dirty && !confirm('You have unsaved changes. Leave without saving?')) e.preventDefault();
  });

  renderAlerts();
})();
