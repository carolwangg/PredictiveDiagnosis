/*
 * Form page <-> ems_encounters.json.
 * Uses the ACR field API; the server (encounter_store.py) does the persistence.
 */
(function () {
  'use strict';
  var dataEl = document.getElementById('enc-data');
  if (!dataEl || !window.ACR) return;

  var meta = JSON.parse(dataEl.textContent);   // {id, incident_no, signed, api, home, page, ...}
  var dirty = false, busy = false, editVersion = 0, saveTimer = null;
  var $ = function (id) { return document.getElementById(id); };
  var stateEl = $('save-state'), saveBtn = $('btn-save'), signBtn = $('btn-sign'), pill = $('status-pill');
  var importButton = $('btn-import-audio');
  var textScribeButton = $('btn-scribe-text');
  var transcriptButton = $('btn-transcript');
  var transcriptSidebar = $('transcript-sidebar');
  var transcriptInput = $('scribe-text');
  var transcriptReview = $('transcript-review');
  var transcriptStatus = $('transcript-status');
  var pendingScribeSection = null;

  function setState(text, cls) { stateEl.textContent = text; stateEl.className = 'save-state ' + (cls || ''); }

  function scheduleSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      if (!busy) save().catch(function () {});
    }, 500);
  }

  function toggleTranscript(open, focusInput) {
    transcriptSidebar.hidden = !open;
    document.body.classList.toggle('transcript-open', open);
    transcriptButton.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open && focusInput !== false) transcriptInput.focus();
  }

  function updateTranscriptReview() {
    var transcript = transcriptInput.value;
    var patterns = [
      /\b(?:on\s+)?wound\s+error\b/gi,
      /\bquestion\s+(?:chest\s+pain|shortness\s+of\s+breath|difficulty\s+breathing)\b/gi,
      /\b([a-z]{2,})\s+\1\b/gi,
      /\[(?:inaudible|unclear)\]/gi
    ];
    var matches = [];
    patterns.forEach(function (pattern) {
      var match;
      while ((match = pattern.exec(transcript))) {
        matches.push({ start: match.index, end: match.index + match[0].length });
      }
    });
    matches.sort(function (a, b) { return a.start - b.start || b.end - a.end; });
    var filtered = [];
    matches.forEach(function (match) {
      var previous = filtered[filtered.length - 1];
      if (!previous || match.start >= previous.end) filtered.push(match);
    });

    transcriptReview.replaceChildren();
    var cursor = 0;
    filtered.forEach(function (match) {
      transcriptReview.appendChild(document.createTextNode(transcript.slice(cursor, match.start)));
      var mark = document.createElement('mark');
      mark.textContent = transcript.slice(match.start, match.end);
      mark.dataset.start = match.start;
      mark.dataset.end = match.end;
      transcriptReview.appendChild(mark);
      cursor = match.end;
    });
    transcriptReview.appendChild(document.createTextNode(transcript.slice(cursor)));
  }

  function showTranscript(text, status) {
    transcriptInput.value = text || '';
    updateTranscriptReview();
    transcriptStatus.textContent = status;
    toggleTranscript(true, false);
  }

  transcriptButton.addEventListener('click', function () {
    toggleTranscript(transcriptSidebar.hidden);
  });
  $('btn-transcript-close').addEventListener('click', function () {
    toggleTranscript(false);
    transcriptButton.focus();
  });
  transcriptInput.addEventListener('input', function () {
    updateTranscriptReview();
    transcriptStatus.textContent = 'Transcript edited. Review highlighted wording before sending.';
  });
  transcriptReview.addEventListener('click', function (event) {
    var mark = event.target.closest('mark');
    if (!mark) return;
    transcriptInput.focus();
    transcriptInput.setSelectionRange(Number(mark.dataset.start), Number(mark.dataset.end));
  });

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
    if (textScribeButton) textScribeButton.disabled = !!r.signed || busy;
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
    var savedVersion = editVersion;
    busy = true;
    if (saveBtn) saveBtn.disabled = true;
    setState('Saving…');
    var creating = !meta.id;
    return api(creating ? 'POST' : 'PUT', creating ? meta.api : meta.api + '/' + meta.id, payload())
      .then(function (r) {
        if (creating) history.replaceState(null, '', meta.page + '/' + r.id);
        applyResult(r);
        dirty = editVersion !== savedVersion;
        setState(dirty ? 'Saving newer changes…' :
          'Saved ' + new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
        return r;
      })
      .catch(function (e) { setState('Save failed – ' + e.message, 'err'); throw e; })
      .then(function (r) {
        busy = false;
        if (saveBtn) saveBtn.disabled = false;
        if (dirty) scheduleSave();
        return r;
      }, function (e) {
        busy = false;
        if (saveBtn) saveBtn.disabled = false;
        throw e;
      });
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

  if (saveBtn) saveBtn.addEventListener('click', function () { save().catch(function () {}); });
  signBtn.addEventListener('click', toggleSign);

  ACRAudio.attach($('btn-record'), {
    isDisabled: function () { return !!meta.signed; },
    url: function () { return meta.api + '/' + encodeURIComponent(meta.id) + '/audio'; },
    liveUrl: function () { return meta.api + '/' + encodeURIComponent(meta.id) + '/transcribe'; },
    beforeStart: function () {
      if (meta.signed) return Promise.reject(new Error('Unpost this report before recording an update.'));
      return dirty || !meta.id ? save() : Promise.resolve();
    },
    onTranscript: function (transcript) {
      showTranscript(transcript, 'Live transcription. Review highlighted wording as you speak.');
    },
    onResult: function (result) {
      pendingScribeSection = result.section || null;
      showTranscript(
        result.transcript,
        result.awaiting_review
          ? 'Transcription ready. Review it, then send it to the scribe.'
          : 'Transcript extracted and applied to the report.'
      );
      if (result.awaiting_review) {
        setState('Transcript ready for review');
        return;
      }
      ACR.clear();
      ACR.populate(result.form || {});
      applyResult(result);
      dirty = false;
      setState('Voice update saved');
    },
    onState: function (state) {
      if (state === 'recording') {
        toggleTranscript(true, false);
        transcriptStatus.textContent = 'Listening. Live transcript will update as audio is recognized.';
      } else if (state === 'processing') {
        toggleTranscript(true, false);
        transcriptStatus.textContent = 'Finalizing transcription…';
      }
    },
    onLiveError: function () {
      transcriptStatus.textContent = 'Live transcription is unavailable. The full recording will still be transcribed when you stop.';
    },
    onError: function (error) {
      transcriptStatus.textContent = 'Transcription failed.';
      setState('Voice update failed – ' + error.message, 'err');
    }
  });

  document.querySelectorAll('[data-scribe-section]').forEach(function (button) {
    var section = button.dataset.scribeSection;
    ACRAudio.attach(button, {
      isDisabled: function () { return !!meta.signed; },
      url: function () { return meta.api + '/' + encodeURIComponent(meta.id) + '/audio'; },
      liveUrl: function () { return meta.api + '/' + encodeURIComponent(meta.id) + '/transcribe'; },
      formFields: function () { return { section: section }; },
      labels: {
        idle: 'Record ' + section.replace(/-/g, ' ') + ' section',
        requesting: 'Connecting to microphone',
        recording: 'Stop section recording',
        processing: 'Transcribing section recording'
      },
      beforeStart: function () {
        if (meta.signed) return Promise.reject(new Error('Unpost this report before recording an update.'));
        return dirty || !meta.id ? save() : Promise.resolve();
      },
      onTranscript: function (transcript) {
        showTranscript(transcript, 'Live section transcript. Highlighted wording may need review.');
      },
      onResult: function (result) {
        pendingScribeSection = result.section || section;
        showTranscript(
          result.transcript,
          result.awaiting_review
            ? 'Section transcription ready. Review it, then send it to the scribe.'
            : 'Section transcript extracted and applied to the report.'
        );
        if (result.awaiting_review) {
          setState('Section transcript ready for review');
          return;
        }
        ACR.clear();
        ACR.populate(result.form || {});
        applyResult(result);
        dirty = false;
        pendingScribeSection = null;
        setState('Section voice update saved');
      },
      onState: function (state) {
        if (state === 'recording') {
          pendingScribeSection = section;
          toggleTranscript(true, false);
          transcriptStatus.textContent = 'Listening for the ' + section.replace(/-/g, ' ') + ' section.';
        } else if (state === 'processing') {
          toggleTranscript(true, false);
          transcriptStatus.textContent = 'Finalizing the section transcription…';
        }
      },
      onError: function (error) {
        transcriptStatus.textContent = 'Section transcription failed.';
        setState('Section voice update failed – ' + error.message, 'err');
      },
      onLiveError: function () {
        transcriptStatus.textContent = 'Live transcription is unavailable. The final section recording will still be processed when you stop.';
      }
    });
  });

  function processAudioFile(file, section) {
    if (!file) return;
    if (meta.signed) {
      setState('Unpost this report before importing audio.', 'err');
      return;
    }
    var recordButtons = Array.prototype.slice.call(
      document.querySelectorAll('#btn-record, [data-scribe-section]')
    );
    if (recordButtons.some(function (button) {
      return button.dataset.state && button.dataset.state !== 'idle';
    })) {
      setState('Stop recording before importing an audio file.', 'err');
      return;
    }
    if (busy) return;

    var uploadButtons = Array.prototype.slice.call(
      document.querySelectorAll('#btn-import-audio, [data-scribe-upload]')
    );
    uploadButtons.forEach(function (button) { button.disabled = true; });
    recordButtons.forEach(function (button) { button.disabled = true; });
    if (textScribeButton) textScribeButton.disabled = true;
    signBtn.disabled = true;

    var saved = dirty || !meta.id ? save() : Promise.resolve();
    saved.then(function () {
      busy = true;
      if (saveBtn) saveBtn.disabled = true;
      setState('Processing uploaded audio…');
      showTranscript(
        '',
        section ? 'Section audio is being transcribed…' : 'Audio file is being transcribed…'
      );
      var body = new FormData();
      body.append('audio', file, file.name);
      body.append('source', 'file');
      if (section) body.append('section', section);
      return fetch(meta.api + '/' + encodeURIComponent(meta.id) + '/audio', {
        method: 'POST',
        headers: { Accept: 'application/json' },
        body: body
      }).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (result) {
          if (!response.ok) throw new Error(result.error || ('HTTP ' + response.status));
          if (result.awaiting_review) {
            pendingScribeSection = result.section || section || null;
            showTranscript(
              result.transcript,
              section
                ? 'Section transcription ready. Review it, then send it to the scribe.'
                : 'Transcription ready. Review it, then send it to the scribe.'
            );
            setState(section ? 'Section transcript ready for review' : 'Transcript ready for review');
            return;
          }
          showTranscript(result.transcript, 'Transcript extracted and applied to the report.');
          ACR.clear();
          ACR.populate(result.form || {});
          applyResult(result);
          dirty = false;
          pendingScribeSection = null;
          setState(section ? 'Section audio processed' : 'Uploaded audio processed');
        });
      });
    }).catch(function (error) {
      transcriptStatus.textContent = 'Audio transcription failed.';
      setState('Audio upload failed – ' + error.message, 'err');
    }).then(function () {
      busy = false;
      uploadButtons.forEach(function (button) { button.disabled = !!meta.signed; });
      recordButtons.forEach(function (button) { button.disabled = !!meta.signed; });
      if (textScribeButton) textScribeButton.disabled = !!meta.signed;
      if (saveBtn) saveBtn.disabled = false;
      signBtn.disabled = false;
    });
  }

  if (importButton) {
    var audioFile = $('audio-file');
    importButton.addEventListener('click', function () { audioFile.click(); });
    audioFile.addEventListener('change', function () {
      var file = audioFile.files && audioFile.files[0];
      audioFile.value = '';
      processAudioFile(file, null);
    });
  }

  document.querySelectorAll('[data-scribe-upload]').forEach(function (button) {
    var fileInput = button.parentElement.querySelector('[data-scribe-audio-file]');
    button.addEventListener('click', function () { fileInput.click(); });
    fileInput.addEventListener('change', function () {
      var file = fileInput.files && fileInput.files[0];
      fileInput.value = '';
      processAudioFile(file, button.dataset.scribeUpload);
    });
  });

  if (textScribeButton) {
    textScribeButton.addEventListener('click', function () {
      var transcript = transcriptInput.value.trim();
      if (!transcript) {
        setState('Enter transcript text before sending it to the scribe.', 'err');
        transcriptInput.focus();
        return;
      }
      if (meta.signed) {
        setState('Unpost this report before sending text to the scribe.', 'err');
        return;
      }
      var recordingState = $('btn-record').dataset.state;
      if (recordingState && recordingState !== 'idle') {
        setState('Stop recording before sending transcript text.', 'err');
        return;
      }
      if (busy) return;

      textScribeButton.disabled = true;
      $('btn-record').disabled = true;
      if (importButton) importButton.disabled = true;
      var saved = dirty || !meta.id ? save() : Promise.resolve();
      saved.then(function () {
        busy = true;
        if (saveBtn) saveBtn.disabled = true;
        signBtn.disabled = true;
        setState('Sending text to scribe…');
        return api(
          'POST',
          meta.api + '/' + encodeURIComponent(meta.id) + '/text',
          Object.assign(
            { transcript: transcript },
            pendingScribeSection ? { section: pendingScribeSection } : {}
          )
        ).then(function (result) {
          ACR.clear();
          ACR.populate(result.form || {});
          applyResult(result);
          dirty = false;
          updateTranscriptReview();
          transcriptStatus.textContent = 'Transcript sent to the scribe and applied to the report.';
          pendingScribeSection = null;
          setState('Text processed by scribe');
        });
      }).catch(function (error) {
        transcriptStatus.textContent = 'Transcript could not be processed.';
        setState('Text scribe failed – ' + error.message, 'err');
      }).then(function () {
        busy = false;
        textScribeButton.disabled = !!meta.signed;
        if (importButton) importButton.disabled = !!meta.signed;
        if (saveBtn) saveBtn.disabled = false;
        signBtn.disabled = false;
        $('btn-record').disabled = !!meta.signed;
      });
    });
  }

  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save().catch(function () {}); }
  });
  ACR.onChange(function (change) {
    if (change.source !== 'user') return;
    editVersion += 1;
    dirty = true;
    setState('Unsaved changes', 'dirty');
    scheduleSave();
  });
  window.addEventListener('beforeunload', function (e) { if (dirty) { e.preventDefault(); e.returnValue = ''; } });

  // intercept the "All reports" link so unsaved edits aren't lost silently
  document.querySelector('#appbar .back').addEventListener('click', function (e) {
    if (!dirty) return;
    e.preventDefault();
    var destination = e.currentTarget.href;
    clearTimeout(saveTimer);
    save().then(function () {
      window.location.assign(destination);
    }).catch(function () {});
  });

  renderAlerts();
})();
