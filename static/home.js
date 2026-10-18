/* Home page: list, select, add / update (open form) / remove call reports in ems_encounters.json */
(function () {
  'use strict';
  var cfg = window.ACR_HOME, all = [], filter = 'all', query = '', selected = null;
  var $ = function (id) { return document.getElementById(id); };
  var listEl = $('list'), emptyEl = $('empty'), bar = $('actionbar');
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }

  var CLOUD = '<path d="M7 18a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 8.5a4.75 4.75 0 0 1-.5 9.5z"/>';
  var ICON_UNPOSTED = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' + CLOUD.replace('<path', '<path opacity=".55"') +
    '<rect x="9" y="12" width="6" height="1.8" rx=".9" fill="#fff"/></svg>';
  var ICON_POSTED = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' + CLOUD +
    '<path d="M9.2 13l2 2 3.6-3.8" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function parseWhen(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/.exec(s || '');
    return m ? { mon: MONTHS[+m[2] - 1], day: +m[3], time: m[4] ? m[4] + ':' + m[5] : '' } : { mon: '', day: '', time: '' };
  }

  /* ---------- render ---------- */
  function visible() {
    var q = query.trim().toLowerCase();
    return all.filter(function (e) {
      if (filter === 'posted' && !e.signed) return false;
      if (filter === 'unposted' && e.signed) return false;
      if (!q) return true;
      return [e.incident_no, e.name, e.address.join(' '), e.chief_complaint, e.protocols.join(' ')]
        .join(' ').toLowerCase().indexOf(q) !== -1;
    });
  }

  function card(e, i) {
    var w = parseWhen(e.when), a = e.address;
    var tags = '';
    if (e.missing_fields.length) tags += '<span class="tag alert" title="' + esc(e.missing_fields.join('\n')) + '">' + e.missing_fields.length + (e.missing_fields.length === 1 ? ' alert' : ' alerts') + '</span>';
    e.protocols.forEach(function (p) { tags += '<span class="tag proto">' + esc(p) + '</span>'; });
    var who = [e.name, e.sex && e.age ? e.sex + ', ' + e.age : '', e.chief_complaint].filter(Boolean).join(' · ');
    return '<li class="card ' + (e.signed ? 'posted' : '') + (e.encounter_id === selected ? ' selected' : '') + '" tabindex="0" role="button" ' +
      'aria-pressed="' + (e.encounter_id === selected) + '" data-id="' + esc(e.encounter_id) + '" style="animation-delay:' + Math.min(i, 10) * 35 + 'ms">' +
      '<div class="inc"><small>Incident #</small><b>' + esc(e.incident_no) + '</b></div>' +
      '<div class="state ' + (e.signed ? 'posted' : 'unposted') + '">' + (e.signed ? ICON_POSTED : ICON_UNPOSTED) +
        '<span>' + (e.signed ? 'Posted' : 'Unposted') + '<br>Post Incident</span></div>' +
      '<div class="addr"><div class="l1">' + esc(a[0] || 'No address recorded') + '</div>' +
        (a[1] ? '<div class="l2">' + esc(a[1]) + '</div>' : '') +
        '<div class="meta">' + (who ? '<span>' + esc(who) + '</span>' : '') + tags + '</div></div>' +
      (e.age ? '<div class="badge" title="Patient age">' + esc(e.age) + '</div>' : '<div class="badge na" title="Age not recorded">–</div>') +
      '<div class="when"><span><span class="mon">' + esc(w.mon) + '</span><span class="day">' + esc(w.day) + '</span></span>' +
        '<div class="time">' + esc(w.time) + '</div></div>' +
      '<a class="go" href="' + cfg.page + '/' + encodeURIComponent(e.encounter_id) + '" aria-label="Open incident ' + esc(e.incident_no) + '">' +
        '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg></a>' +
      '</li>';
  }

  function render() {
    var rows = visible();
    listEl.innerHTML = rows.map(card).join('');
    emptyEl.hidden = rows.length > 0;
    if (!rows.length) {
      $('empty-title').textContent = all.length ? 'No matching reports' : 'No call reports yet';
      $('empty-text').textContent = all.length ? 'Try a different search or filter.' : 'Start a new incident to create the first one.';
    }
    var posted = all.filter(function (e) { return e.signed; }).length;
    $('n-all').textContent = all.length; $('n-posted').textContent = posted; $('n-unposted').textContent = all.length - posted;
    $('subtitle').textContent = all.length + (all.length === 1 ? ' report' : ' reports') + ' · ' + (all.length - posted) + ' unposted';
    renderBar();
  }

  function current() { return all.filter(function (e) { return e.encounter_id === selected; })[0]; }

  function renderBar() {
    var e = current();
    bar.hidden = !e;
    if (!e) return;
    $('sel-title').textContent = 'Incident ' + e.incident_no;
    $('sel-sub').textContent = [e.name, e.chief_complaint].filter(Boolean).join(' · ') || (e.address[0] || '');
    $('act-open').href = cfg.page + '/' + encodeURIComponent(e.encounter_id);
    $('act-record').disabled = !!e.signed && $('act-record').dataset.state !== 'recording';
    $('act-record').title = e.signed ? 'Unpost this report before recording an update' : 'Record an update for this incident';
  }

  function select(id) {
    if ($('act-record').dataset.state && $('act-record').dataset.state !== 'idle') return;
    selected = id === selected ? null : id;
    Array.prototype.forEach.call(listEl.children, function (li) {
      var on = li.dataset.id === selected;
      li.classList.toggle('selected', on); li.setAttribute('aria-pressed', on);
    });
    renderBar();
  }

  /* ---------- data ---------- */
  function toast(msg, err) {
    var t = $('toast'); t.textContent = msg; t.className = 'toast' + (err ? ' err' : ''); t.hidden = false;
    clearTimeout(toast.h); toast.h = setTimeout(function () { t.hidden = true; }, 2600);
  }

  ACRAudio.attach($('act-record'), {
    isDisabled: function () {
      var e = current();
      return !e || !!e.signed;
    },
    url: function () {
      var e = current();
      if (!e) throw new Error('Select an incident before recording.');
      return cfg.api + '/' + encodeURIComponent(e.encounter_id) + '/audio';
    },
    onResult: function (result) {
      toast('Voice update saved for incident ' + result.incident_no + '.');
      return load();
    },
    onError: function (error) { toast('Voice update failed – ' + error.message, true); }
  });
  bar.addEventListener('click', function (event) {
    var state = $('act-record').dataset.state;
    if (state && state !== 'idle' && !event.target.closest('#act-record')) {
      event.preventDefault();
      event.stopImmediatePropagation();
      toast('Stop recording before switching incidents.', true);
    }
  }, true);

  function load() {
    return fetch(cfg.api, { headers: { Accept: 'application/json' } })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (d) {
        all = d.encounters;
        if (selected && !current()) selected = null;
        render();
        return true;
      })
      .catch(function (e) {
        $('subtitle').textContent = 'Could not load reports';
        toast('Could not load reports – ' + e.message, true);
        return false;
      });
  }

  $('btn-refresh').addEventListener('click', function () {
    var button = $('btn-refresh');
    if (button.disabled) return;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    load().then(function (loaded) {
      if (loaded) toast('Incident list refreshed');
    }).finally(function () {
      button.disabled = false;
      button.setAttribute('aria-busy', 'false');
    });
  });

  /* ---------- events ---------- */
  listEl.addEventListener('click', function (ev) {
    if ($('act-record').dataset.state && $('act-record').dataset.state !== 'idle') {
      if (ev.target.closest('a.go')) ev.preventDefault();
      return;
    }
    if (ev.target.closest('a.go')) return;             // arrow button navigates
    var li = ev.target.closest('.card'); if (li) select(li.dataset.id);
  });
  listEl.addEventListener('dblclick', function (ev) {
    if ($('act-record').dataset.state && $('act-record').dataset.state !== 'idle') return;
    var li = ev.target.closest('.card'); if (li) location.href = cfg.page + '/' + encodeURIComponent(li.dataset.id);
  });
  listEl.addEventListener('keydown', function (ev) {
    if ($('act-record').dataset.state && $('act-record').dataset.state !== 'idle') return;
    var li = ev.target.closest('.card'); if (!li) return;
    if (ev.key === ' ') { ev.preventDefault(); select(li.dataset.id); }
    else if (ev.key === 'Enter' && ev.target === li) location.href = cfg.page + '/' + encodeURIComponent(li.dataset.id);
  });

  $('q').addEventListener('input', function (e) { query = e.target.value; render(); });
  Array.prototype.forEach.call(document.querySelectorAll('.chip'), function (c) {
    c.addEventListener('click', function () {
      filter = c.dataset.filter;
      Array.prototype.forEach.call(document.querySelectorAll('.chip'), function (x) { x.classList.toggle('on', x === c); });
      render();
    });
  });

  $('act-clear').addEventListener('click', function () {
    var state = $('act-record').dataset.state;
    if ((!state || state === 'idle') && selected) select(selected);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && selected && !$('confirm').open &&
        (!$('act-record').dataset.state || $('act-record').dataset.state === 'idle')) select(selected);
  });

  // remove
  var dlg = $('confirm');
  $('act-del').addEventListener('click', function () {
    var e = current(); if (!e) return;
    $('confirm-text').textContent = 'Incident ' + e.incident_no + (e.name ? ' (' + e.name + ')' : '') +
      ' will be permanently removed from ems_encounters.json. This cannot be undone.';
    dlg.showModal();
  });
  dlg.addEventListener('close', function () {
    if (dlg.returnValue !== 'ok') return;
    var e = current(); if (!e) return;
    fetch(cfg.api + '/' + encodeURIComponent(e.encounter_id), { method: 'DELETE' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); })
      .then(function () { toast('Incident ' + e.incident_no + ' removed'); selected = null; return load(); })
      .catch(function (err) { toast('Remove failed – ' + err.message, true); });
  });

  window.addEventListener('pageshow', function (e) { if (e.persisted) load(); });   // back button after editing
  load();
})();
