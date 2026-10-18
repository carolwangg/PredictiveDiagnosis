/*
 * Ambulance Call Report – field API.
 *
 * Every editable thing in the page carries data-field="<name>".  The same name may appear
 * more than once (e.g. demographics are printed on several pages); copies stay in sync.
 *
 * Value formats
 *   text / textarea : string
 *   check           : true | false
 *   radio group     : the selected option's value, or ""
 *   date            : "YYYY-MM-DD"   (partial entries come back as e.g. "2026--")
 *   time            : "HH:MM" or "HH:MM:SS"
 *
 * Public API  (window.ACR)
 *   ACR.set(name, value, {silent})   -> boolean (false if the field does not exist)
 *   ACR.get(name)                    -> value, or undefined if unknown field
 *   ACR.populate({name: value, ...}) -> {set: [...], missing: [...]}
 *   ACR.collect({skipEmpty})         -> {name: value, ...}
 *   ACR.clear()
 *   ACR.focus(name)                  -> scrolls to the field and focuses it
 *   ACR.schema()                     -> [{name, type, label}, ...]
 *   ACR.has(name)
 *   ACR.onChange(fn)                 -> fn({name, value, source}) on every edit
 *   ACR.addProcedureRow()            -> new row number
 *   ACR.setProcedures([{time, procedure, ...}, ...])
 *   ACR.getProcedures()              -> [{time, procedure, ...}, ...]  (non-empty rows only)
 *   ACR.load(url) / ACR.save(url)    -> fetch() helpers (JSON)
 *
 * window.ACR_PREFILL, if defined before DOMContentLoaded, is passed to populate().
 */
(function (global) {
  'use strict';

  var TRUE_RE = /^(true|1|on|yes|y|x|checked)$/i;
  var PROC_COLUMNS = ['time', 'procedure', 'crew', 'reading', 'result_code', 'resp_rate',
    'pulse_rate', 'bp', 'temp', 'spo2', 'pupil_r', 'pupil_l', 'dose', 'route', 'gcs', 'pain'];
  var index = null;

  /* ---------- registry ---------- */
  function typeOf(el) {
    if (el.dataset.type) return el.dataset.type;
    if (el.tagName === 'TEXTAREA') return 'textarea';
    if (el.tagName === 'SELECT') return 'select';
    if (el.type === 'checkbox') return 'check';
    if (el.type === 'radio') return 'radio';
    return 'text';
  }

  function build() {
    index = new Map();
    document.querySelectorAll('[data-field]').forEach(function (el) {
      var name = el.dataset.field;
      if (!index.has(name)) index.set(name, { name: name, type: typeOf(el), els: [] });
      index.get(name).els.push(el);
    });
    return index;
  }
  function fields() { return index || build(); }

  /* ---------- reading / writing one element ---------- */
  function parts(el) { return Array.prototype.slice.call(el.querySelectorAll('[data-part]')); }
  function truthy(v) { return v === true || TRUE_RE.test(String(v)); }

  function readEl(type, el) {
    if (type === 'check') return el.checked;
    if (type === 'date' || type === 'time') {
      var vals = parts(el).map(function (p) { return p.value.trim(); });
      if (!vals.some(Boolean)) return '';
      return vals.join(type === 'date' ? '-' : ':');
    }
    return el.value;
  }

  function splitPacked(s, ps) {
    var out = [], pos = 0;
    ps.forEach(function (p) { var n = +p.maxLength; out.push(s.substr(pos, n)); pos += n; });
    return out;
  }

  function writeEl(type, el, v) {
    if (v === null || v === undefined) v = '';
    if (type === 'check') {
      el.checked = truthy(v);
    } else if (type === 'radio') {
      el.checked = String(v) !== '' && String(v) === el.value;
    } else if (type === 'date' || type === 'time') {
      var ps = parts(el);
      var nums = String(v).match(/\d+/g) || [];
      if (nums.length === 1 && ps.length > 1) nums = splitPacked(nums[0], ps);
      ps.forEach(function (p, i) { p.value = nums[i] || ''; });
    } else {
      el.value = String(v);
    }
  }

  function read(f) {
    if (f.type === 'radio') {
      var hit = f.els.filter(function (e) { return e.checked; })[0];
      return hit ? hit.value : '';
    }
    return readEl(f.type, f.els[0]);
  }

  /* ---------- events ---------- */
  function emit(f, source) {
    document.dispatchEvent(new CustomEvent('acr:change', {
      detail: { name: f.name, value: read(f), source: source }
    }));
  }

  function userChanged(host) {
    var f = fields().get(host.dataset.field);
    if (!f) return;
    if (f.type !== 'radio') {
      var v = readEl(f.type, host);
      f.els.forEach(function (el) { if (el !== host) writeEl(f.type, el, v); });
    }
    emit(f, 'user');
  }

  /* ---------- public API ---------- */
  function has(name) { return fields().has(name); }

  function get(name) {
    var f = fields().get(name);
    return f ? read(f) : undefined;
  }

  function set(name, value, opts) {
    var f = fields().get(name);
    if (!f) return false;
    f.els.forEach(function (el) { writeEl(f.type, el, value); });
    if (!opts || !opts.silent) emit(f, 'api');
    return true;
  }

  function populate(obj) {
    var result = { set: [], missing: [] };
    Object.keys(obj || {}).forEach(function (name) {
      if (name === 'procedures' && Array.isArray(obj[name])) { setProcedures(obj[name]); return; }
      (set(name, obj[name], { silent: true }) ? result.set : result.missing).push(name);
    });
    document.dispatchEvent(new CustomEvent('acr:populate', { detail: result }));
    return result;
  }

  function collect(opts) {
    var skipEmpty = opts && opts.skipEmpty, out = {};
    fields().forEach(function (f, name) {
      var v = read(f);
      if (skipEmpty && (v === '' || v === false)) return;
      out[name] = v;
    });
    return out;
  }

  function clear() {
    fields().forEach(function (f) { f.els.forEach(function (el) { writeEl(f.type, el, ''); }); });
    document.dispatchEvent(new CustomEvent('acr:clear'));
  }

  function labelOf(f) {
    var el = f.els[0];
    var chk = el.closest('label.chk');
    if (chk) return chk.textContent.trim();
    var cell = el.closest('.cell');
    var lbl = cell && cell.querySelector('.lbl');
    if (lbl) return lbl.textContent.trim();
    var th = el.closest('td') && document.querySelectorAll('table.proc thead th')[el.closest('td').cellIndex];
    return th ? th.textContent.trim() : '';
  }

  function schema() {
    var out = [];
    fields().forEach(function (f, name) { out.push({ name: name, type: f.type, label: labelOf(f) }); });
    return out;
  }

  function focus(name) {
    var f = fields().get(name);
    if (!f) return false;
    var el = f.els[0];
    var target = el.matches('[data-part]') ? el : (el.querySelector('[data-part]') || el);
    target.scrollIntoView({ block: 'center', behavior: 'smooth' });
    target.focus({ preventScroll: true });
    var cell = el.closest('.cell');
    if (cell) { cell.classList.remove('flash'); void cell.offsetWidth; cell.classList.add('flash'); }
    return true;
  }

  function onChange(fn) {
    document.addEventListener('acr:change', function (e) { fn(e.detail); });
  }

  /* ---------- procedure rows ---------- */
  function procBody() { return document.getElementById('proc-body'); }
  function rowCount() { var b = procBody(); return b ? b.children.length : 0; }

  function addProcedureRow() {
    var body = procBody();
    if (!body || !body.lastElementChild) return 0;
    var last = body.lastElementChild;
    var n = (+last.dataset.row) + 1;
    var row = last.cloneNode(true);
    row.dataset.row = n;
    row.querySelectorAll('[data-field]').forEach(function (el) {
      el.dataset.field = el.dataset.field.replace(/^proc_\d+_/, 'proc_' + n + '_');
    });
    row.querySelectorAll('input').forEach(function (el) { el.value = ''; });
    row.querySelector('.rownum').textContent = n;
    body.appendChild(row);
    build();
    return n;
  }

  /* rows with at least one filled cell -> [{time, procedure, ...}] (empty cells omitted) */
  function getProcedures() {
    var out = [];
    for (var i = 1; i <= rowCount(); i++) {
      var row = {}, any = false;
      PROC_COLUMNS.forEach(function (col) {
        var v = get('proc_' + i + '_' + col);
        if (v !== undefined && v !== '' && v !== false) { row[col] = v; any = true; }
      });
      if (any) out.push(row);
    }
    return out;
  }

  function setProcedures(rows) {
    while (rowCount() < rows.length) addProcedureRow();
    rows.forEach(function (row, i) {
      PROC_COLUMNS.forEach(function (col) {
        if (row[col] !== undefined) set('proc_' + (i + 1) + '_' + col, row[col], { silent: true });
      });
    });
    document.dispatchEvent(new CustomEvent('acr:populate', { detail: { procedures: rows.length } }));
  }

  /* ---------- fetch helpers ---------- */
  function load(url) {
    return fetch(url, { headers: { Accept: 'application/json' } })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (data) { populate(data); return data; });
  }

  function save(url, opts) {
    opts = opts || {};
    return fetch(url, {
      method: opts.method || 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(collect(opts))
    }).then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
  }

  /* ---------- user interaction ---------- */
  function nextPart(el, dir) {
    var ps = parts(el.closest('[data-type]'));
    var i = ps.indexOf(el) + dir;
    if (i >= 0 && i < ps.length) { ps[i].focus(); ps[i].select(); return true; }
    return false;
  }

  document.addEventListener('input', function (e) {
    var el = e.target;
    if (el.type === 'checkbox' || el.type === 'radio') return;   // handled on 'change'
    var host = el.closest('[data-field]');
    if (!host) return;
    if (el.hasAttribute('data-part')) {
      var clean = el.value.replace(/\D/g, '');
      if (clean !== el.value) el.value = clean;
      userChanged(host);
      if (clean.length >= el.maxLength) nextPart(el, 1);
      return;
    }
    userChanged(host);
  });

  document.addEventListener('change', function (e) {
    var el = e.target;
    if (el.type !== 'checkbox' && el.type !== 'radio' && el.tagName !== 'SELECT') return;
    var host = el.closest('[data-field]');
    if (host) userChanged(host);
  });

  document.addEventListener('keydown', function (e) {
    var el = e.target;
    if (!el.hasAttribute || !el.hasAttribute('data-part')) return;
    if (e.key === 'Backspace' && el.value === '') { if (nextPart(el, -1)) e.preventDefault(); }
    else if (/^[\/:\-. ]$/.test(e.key)) { e.preventDefault(); if (el.value) nextPart(el, 1); }
    else if (e.key === 'ArrowLeft' && el.selectionStart === 0) { if (nextPart(el, -1)) e.preventDefault(); }
    else if (e.key === 'ArrowRight' && el.selectionEnd === el.value.length) { if (nextPart(el, 1)) e.preventDefault(); }
  });

  document.addEventListener('paste', function (e) {
    var el = e.target;
    if (!el.hasAttribute || !el.hasAttribute('data-part')) return;
    var text = (e.clipboardData || global.clipboardData).getData('text');
    var nums = text.match(/\d+/g) || [];
    var host = el.closest('[data-field]');
    var total = parts(host).reduce(function (n, p) { return n + (+p.maxLength); }, 0);
    if (nums.length > 1 || (nums[0] && nums[0].length >= total - 1)) {
      e.preventDefault();
      writeEl(typeOf(host), host, text);
      userChanged(host);
    }
  });

  // pad "5" -> "05" when leaving a two-digit part
  document.addEventListener('focusout', function (e) {
    var el = e.target;
    if (!el.hasAttribute || !el.hasAttribute('data-part')) return;
    if (el.maxLength === 2 && el.value.length === 1) {
      el.value = '0' + el.value;
      var host = el.closest('[data-field]');
      if (host) userChanged(host);
    }
  });

  // click a selected radio again to clear it
  function radioOf(t) {
    if (t.type === 'radio') return t;
    var lab = t.closest && t.closest('label.rad');
    return lab ? lab.querySelector('input[type=radio]') : null;
  }
  document.addEventListener('mousedown', function (e) {
    var r = radioOf(e.target);
    if (r) r.dataset.was = r.checked ? '1' : '';
  }, true);
  document.addEventListener('click', function (e) {
    var t = e.target;
    if (t.type === 'radio' && t.dataset.was === '1') { t.checked = false; t.dataset.was = ''; }
  }, true);

  /* ---------- boot ---------- */
  global.ACR = {
    version: '1.0',
    has: has, get: get, set: set, populate: populate, collect: collect, clear: clear,
    focus: focus, schema: schema, onChange: onChange, refresh: build,
    addProcedureRow: addProcedureRow, setProcedures: setProcedures, getProcedures: getProcedures,
    load: load, save: save
  };

  document.addEventListener('DOMContentLoaded', function () {
    build();
    var add = document.getElementById('add-proc-row');
    if (add) add.addEventListener('click', addProcedureRow);
    if (global.ACR_PREFILL) populate(global.ACR_PREFILL);
  });
})(window);
