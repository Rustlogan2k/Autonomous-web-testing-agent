/* Brasswick Asset Register -- console logic shared by every page.
 *
 * State lives in sessionStorage and nowhere else:
 *
 *   brasswick.records   the register: an array of equipment records, seeded on first load
 *   brasswick.counter   the highest record id ever issued (ids are never reused)
 *   brasswick.holders   the custody ledger: record id -> custodian, for every record whose
 *                       custodian has been reassigned since it was registered
 *   brasswick.journal   the change log
 *   brasswick.receipt   which fields the most recent save touched, so the record page can
 *                       read them back from storage and show what was actually stored
 *   brasswick.notice    a one-shot message carried across a page load
 *   brasswick.prefs     display preferences
 *
 * Everything is rendered synchronously while the page loads or inside the event handler
 * that caused it. Nothing here reads the clock, and nothing is random.
 */
(function () {
  'use strict';

  var KEYS = {
    records: 'brasswick.records',
    counter: 'brasswick.counter',
    holders: 'brasswick.holders',
    journal: 'brasswick.journal',
    receipt: 'brasswick.receipt',
    notice: 'brasswick.notice',
    prefs: 'brasswick.prefs'
  };

  var CATEGORY = { instrument: 'Instrument', tooling: 'Tooling', safety: 'Safety gear', computing: 'Computing' };
  var LOCATION = { lab: 'Laboratory', workshop: 'Workshop', stores: 'Stores', annex: 'Annex' };
  var STATUS = { active: 'In service', repair: 'Under repair', loan: 'On loan', retired: 'Retired' };

  var FIELD_LABEL = {
    asset_name: 'Equipment name',
    category: 'Category',
    location: 'Location',
    custodian: 'Custodian',
    units: 'Units held',
    status: 'Status',
    notes: 'Notes'
  };
  var FIELD_SEQUENCE = ['asset_name', 'category', 'location', 'custodian', 'units', 'status', 'notes'];
  /* Fields stored on the record itself. The custodian is not one of them after
     registration: reassignments go to the custody ledger. */
  var RECORD_FIELDS = ['asset_name', 'category', 'location', 'units', 'status', 'notes'];

  var SEED = [
    { id: 1, tag: 'EQ-101', asset_name: 'Bench oscilloscope', category: 'instrument', location: 'lab',
      custodian: 'Priya Raman', units: 2, status: 'active', notes: 'Calibrated each spring.' },
    { id: 2, tag: 'EQ-102', asset_name: 'Torque wrench set', category: 'tooling', location: 'workshop',
      custodian: 'Dmitri Volkov', units: 4, status: 'active', notes: '' },
    { id: 3, tag: 'EQ-103', asset_name: 'Fume extraction hood', category: 'safety', location: 'lab',
      custodian: 'Priya Raman', units: 1, status: 'repair', notes: 'Replacement fan bearing awaited from the supplier.' },
    { id: 4, tag: 'EQ-104', asset_name: 'Thermal camera', category: 'instrument', location: 'stores',
      custodian: 'Aiko Tanabe', units: 1, status: 'loan', notes: 'Lent to the survey team.' },
    { id: 5, tag: 'EQ-105', asset_name: 'Label printer', category: 'computing', location: 'stores',
      custodian: 'Marcus Bell', units: 3, status: 'active', notes: '' },
    { id: 6, tag: 'EQ-106', asset_name: 'Pillar drill', category: 'tooling', location: 'workshop',
      custodian: 'Dmitri Volkov', units: 1, status: 'retired', notes: 'Kept for spare parts.' }
  ];

  /* ---------------------------------------------------------------- storage */

  function readJson(key, fallback) {
    var raw = null;
    try { raw = window.sessionStorage.getItem(key); } catch (ignored) { raw = null; }
    if (raw === null) { return fallback; }
    try { return JSON.parse(raw); } catch (ignoredToo) { return fallback; }
  }

  function writeJson(key, value) {
    try { window.sessionStorage.setItem(key, JSON.stringify(value)); } catch (ignored) { /* storage full or unavailable */ }
  }

  function dropKey(key) {
    try { window.sessionStorage.removeItem(key); } catch (ignored) { /* nothing to drop */ }
  }

  /* --------------------------------------------------------------- register */

  function readRecords() {
    var list = readJson(KEYS.records, null);
    if (!list) {
      list = JSON.parse(JSON.stringify(SEED));
      writeJson(KEYS.records, list);
      writeJson(KEYS.counter, SEED.length);
    }
    return list;
  }

  function positionOf(list, id) {
    for (var i = 0; i < list.length; i += 1) {
      if (list[i].id === id) { return i; }
    }
    return -1;
  }

  function findRecord(id) {
    var list = readRecords();
    var at = positionOf(list, id);
    return at < 0 ? null : list[at];
  }

  /* Write the named fields of one record back to the register. */
  function patchRecord(id, fields, values) {
    var list = readRecords();
    var slot = id - 1;
    var target = list[slot];
    if (!target) { return false; }
    fields.forEach(function (field) { target[field] = values[field]; });
    writeJson(KEYS.records, list);
    return true;
  }

  function registerRecord(values) {
    var list = readRecords();
    var id = readJson(KEYS.counter, SEED.length) + 1;
    var record = { id: id, tag: 'EQ-' + (100 + id) };
    FIELD_SEQUENCE.forEach(function (field) { record[field] = values[field]; });
    list.push(record);
    writeJson(KEYS.records, list);
    writeJson(KEYS.counter, id);
    appendJournal({ record: id, tag: record.tag, action: 'Registered', fields: [], reason: '' });
    return record;
  }

  function removeRecord(id) {
    var record = findRecord(id);
    if (!record) { return null; }
    writeJson(KEYS.records, readRecords().filter(function (row) { return row.id !== id; }));
    var ledger = readJson(KEYS.holders, {});
    delete ledger[String(id)];
    writeJson(KEYS.holders, ledger);
    appendJournal({ record: id, tag: record.tag, action: 'Removed', fields: [], reason: '' });
    return record;
  }

  /* ---------------------------------------------------------------- custody */

  function holderOf(record) {
    var ledger = readJson(KEYS.holders, {});
    var key = String(record.id);
    return Object.prototype.hasOwnProperty.call(ledger, key) ? ledger[key] : record.custodian;
  }

  function assignHolder(id, holder) {
    var ledger = readJson(KEYS.holders, {});
    if (!(String(id) in ledger)) { ledger[String(id)] = holder; }
    writeJson(KEYS.holders, ledger);
  }

  /* ---------------------------------------------------------------- journal */

  function readJournal() { return readJson(KEYS.journal, []); }

  function appendJournal(entry) {
    var journal = readJournal();
    entry.seq = journal.length ? journal[journal.length - 1].seq + 1 : 1;
    journal.push(entry);
    writeJson(KEYS.journal, journal);
  }

  /* ------------------------------------------------------------------ amend */

  /* Save an edit. `values` holds every form field; only the ones that differ from what is
     stored are written, and they are what the receipt lists. */
  function amendRecord(id, values, reason) {
    var before = findRecord(id);
    if (!before) { return { ok: false, why: 'gone' }; }

    var touched = RECORD_FIELDS.filter(function (field) { return before[field] !== values[field]; });
    var reassigned = holderOf(before) !== values.custodian;
    if (!touched.length && !reassigned) { return { ok: false, why: 'unchanged' }; }

    if (touched.length && !patchRecord(id, touched, values)) { return { ok: false, why: 'gone' }; }
    if (reassigned) {
      assignHolder(id, values.custodian);
      touched.push('custodian');
    }

    touched.sort(function (a, b) { return FIELD_SEQUENCE.indexOf(a) - FIELD_SEQUENCE.indexOf(b); });
    appendJournal({ record: id, tag: before.tag, action: 'Amended', fields: touched, reason: reason });
    writeJson(KEYS.receipt, { id: id, fields: touched, reason: reason });
    return { ok: true };
  }

  /* ---------------------------------------------------------------- display */

  function shown(record, field) {
    if (field === 'category') { return CATEGORY[record.category] || record.category; }
    if (field === 'location') { return LOCATION[record.location] || record.location; }
    if (field === 'status') { return STATUS[record.status] || record.status; }
    if (field === 'custodian') { return holderOf(record) || 'Unassigned'; }
    if (field === 'notes') { return record.notes || 'None'; }
    return String(record[field]);
  }

  function byId(id) { return document.getElementById(id); }

  function make(tag, attributes, text) {
    var node = document.createElement(tag);
    if (attributes) {
      Object.keys(attributes).forEach(function (name) { node.setAttribute(name, attributes[name]); });
    }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }

  function empty(node) {
    while (node.firstChild) { node.removeChild(node.firstChild); }
  }

  function showNotice(text) {
    var slot = byId('notice-slot');
    if (!slot) { return; }
    empty(slot);
    slot.appendChild(make('p', { 'class': 'notice', id: 'notice' }, text));
  }

  function carryNotice(text) { writeJson(KEYS.notice, text); }

  function recordIdFromQuery() {
    var raw = new URLSearchParams(window.location.search).get('id');
    return raw !== null && /^\d+$/.test(raw) ? parseInt(raw, 10) : null;
  }

  function fillOptions(select, table) {
    Object.keys(table).forEach(function (value) {
      select.appendChild(make('option', { value: value }, table[value]));
    });
  }

  function missingRecordPanel(stage) {
    var panel = make('section', { 'class': 'panel', id: 'missing-panel' });
    panel.appendChild(make('h2', null, 'No such record'));
    panel.appendChild(make('p', null, 'That record is not on the register. It may have been removed.'));
    panel.appendChild(make('a', { id: 'missing-to-register', 'class': 'btn ghost', href: 'equipment.html' },
      'Back to the equipment register'));
    stage.appendChild(panel);
  }

  /* A confirmation is an ordinary panel in the page, never a native dialog. */
  function confirmRemoval(slot, record, onConfirmed) {
    empty(slot);
    var panel = make('section', { 'class': 'panel confirm', id: 'confirm-panel' });
    panel.appendChild(make('h2', null, 'Remove ' + record.tag + '?'));
    panel.appendChild(make('p', { id: 'confirm-text' },
      record.asset_name + ' will be deleted from the register. The change log keeps a note of the removal.'));
    var row = make('p', { 'class': 'actions' });
    var yes = make('button', { type: 'button', id: 'confirm-remove', 'class': 'danger' }, 'Yes, remove record');
    var no = make('button', { type: 'button', id: 'cancel-remove', 'class': 'ghost' }, 'Keep record');
    yes.addEventListener('click', function () { onConfirmed(); });
    no.addEventListener('click', function () { empty(slot); });
    row.appendChild(yes);
    row.appendChild(no);
    panel.appendChild(row);
    slot.appendChild(panel);
  }

  /* ------------------------------------------------------------ preferences */

  var DEFAULT_PREFS = { compact: false, highlight: true, accent: 'brass' };

  function readPrefs() {
    var stored = readJson(KEYS.prefs, null) || {};
    return {
      compact: typeof stored.compact === 'boolean' ? stored.compact : DEFAULT_PREFS.compact,
      highlight: typeof stored.highlight === 'boolean' ? stored.highlight : DEFAULT_PREFS.highlight,
      accent: typeof stored.accent === 'string' ? stored.accent : DEFAULT_PREFS.accent
    };
  }

  function applyPrefs() {
    var prefs = readPrefs();
    var classes = [];
    if (prefs.compact) { classes.push('density-compact'); }
    if (prefs.highlight) { classes.push('mark-repairs'); }
    if (prefs.accent !== 'brass') { classes.push('accent-' + prefs.accent); }
    document.body.className = classes.join(' ');
  }

  /* ------------------------------------------------------------------ pages */

  function overviewPage() {
    var list = readRecords();
    var tally = { active: 0, repair: 0, loan: 0, retired: 0 };
    var units = 0;
    list.forEach(function (record) {
      tally[record.status] = (tally[record.status] || 0) + 1;
      units += record.units;
    });
    byId('count-records').textContent = String(list.length);
    byId('count-active').textContent = String(tally.active);
    byId('count-repair').textContent = String(tally.repair);
    byId('count-loan').textContent = String(tally.loan);
    byId('count-retired').textContent = String(tally.retired);
    byId('count-units').textContent = String(units);

    var rows = byId('attention-rows');
    var flagged = list.filter(function (record) { return record.status === 'repair' || record.status === 'loan'; });
    flagged.forEach(function (record) {
      var row = make('tr');
      row.appendChild(make('td', { 'class': 'tag' }, record.tag));
      row.appendChild(make('td', null, record.asset_name));
      row.appendChild(make('td', null, shown(record, 'location')));
      row.appendChild(make('td', null, shown(record, 'status')));
      var cell = make('td', { 'class': 'row-actions' });
      cell.appendChild(make('a', { id: 'attention-' + record.id, href: 'record.html?id=' + record.id },
        'Open ' + record.tag));
      row.appendChild(cell);
      rows.appendChild(row);
    });
    if (!flagged.length) {
      var blank = make('tr');
      blank.appendChild(make('td', { colspan: '5' }, 'Nothing is under repair or on loan.'));
      rows.appendChild(blank);
    }

    var feed = byId('latest-changes');
    var journal = readJournal();
    journal.slice(-3).reverse().forEach(function (entry) {
      feed.appendChild(make('li', null, entry.tag + ' — ' + entry.action.toLowerCase()));
    });
    if (!journal.length) { feed.appendChild(make('li', null, 'No changes yet in this session.')); }
  }

  function equipmentPage() {
    var wanted = new URLSearchParams(window.location.search).get('loc');
    var place = Object.prototype.hasOwnProperty.call(LOCATION, wanted) ? wanted : null;
    var statusChoice = 'all';
    var rows = byId('equipment-rows');
    var slot = byId('confirm-slot');

    var scope = byId('scope-line');
    if (place) {
      scope.appendChild(document.createTextNode('Showing equipment kept in ' + LOCATION[place] + '. '));
      scope.appendChild(make('a', { id: 'all-locations', href: 'equipment.html' }, 'Show every location'));
    } else {
      scope.textContent = 'Every location is shown.';
    }

    function render() {
      var list = readRecords().filter(function (record) {
        return (!place || record.location === place) &&
               (statusChoice === 'all' || record.status === statusChoice);
      });
      empty(rows);
      list.forEach(function (record) {
        var row = make('tr');
        if (record.status === 'repair') { row.className = 'needs-repair'; }
        row.appendChild(make('td', { 'class': 'tag' }, record.tag));
        row.appendChild(make('td', null, record.asset_name));
        row.appendChild(make('td', null, shown(record, 'location')));
        row.appendChild(make('td', null, shown(record, 'custodian')));
        row.appendChild(make('td', null, shown(record, 'units')));
        row.appendChild(make('td', null, shown(record, 'status')));
        var cell = make('td', { 'class': 'row-actions' });
        cell.appendChild(make('a', { id: 'view-' + record.id, href: 'record.html?id=' + record.id },
          'View ' + record.tag));
        cell.appendChild(make('a', { id: 'edit-' + record.id, href: 'amend.html?id=' + record.id },
          'Edit ' + record.tag));
        var remove = make('button', { type: 'button', id: 'remove-' + record.id }, 'Remove ' + record.tag);
        remove.addEventListener('click', function () {
          var current = findRecord(record.id);
          if (!current) { return; }
          confirmRemoval(slot, current, function () {
            removeRecord(current.id);
            empty(slot);
            render();
            showNotice(current.tag + ' was removed from the register.');
          });
        });
        cell.appendChild(remove);
        row.appendChild(cell);
        rows.appendChild(row);
      });
      if (!list.length) {
        var blank = make('tr');
        blank.appendChild(make('td', { colspan: '7' }, 'No records match this view.'));
        rows.appendChild(blank);
      }
      byId('row-count').textContent = list.length + (list.length === 1 ? ' record shown' : ' records shown');
    }

    byId('apply-filter').addEventListener('click', function () {
      statusChoice = byId('f-show-status').value;
      empty(slot);
      render();
    });
    render();
  }

  function locationsPage() {
    var list = readRecords();
    var rows = byId('location-rows');
    Object.keys(LOCATION).forEach(function (code) {
      var here = list.filter(function (record) { return record.location === code; });
      var units = here.reduce(function (sum, record) { return sum + record.units; }, 0);
      var row = make('tr');
      row.appendChild(make('td', null, LOCATION[code]));
      row.appendChild(make('td', null, String(here.length)));
      row.appendChild(make('td', null, String(units)));
      var cell = make('td', { 'class': 'row-actions' });
      cell.appendChild(make('a', { id: 'loc-' + code, href: 'equipment.html?loc=' + code },
        'List equipment in ' + LOCATION[code]));
      row.appendChild(cell);
      rows.appendChild(row);
    });
  }

  function journalRow(entry, withTag) {
    var row = make('tr');
    row.appendChild(make('td', null, String(entry.seq)));
    if (withTag) { row.appendChild(make('td', { 'class': 'tag' }, entry.tag)); }
    row.appendChild(make('td', null, entry.action));
    row.appendChild(make('td', null,
      entry.fields.map(function (field) { return FIELD_LABEL[field]; }).join(', ') || '—'));
    row.appendChild(make('td', null, entry.reason || '—'));
    return row;
  }

  function recordPage() {
    var stage = byId('stage');
    var id = recordIdFromQuery();
    var record = id === null ? null : findRecord(id);
    var body = byId('record-body');
    if (!record) {
      body.parentNode.removeChild(body);
      missingRecordPanel(stage);
      return;
    }

    byId('record-heading').textContent = record.tag + ' · ' + record.asset_name;
    document.title = record.tag + ' — Brasswick Asset Register';

    /* The receipt. It lists the fields the last save touched and shows, for each, the
       value now held in storage -- a re-read, not an echo of the form. */
    var receipt = readJson(KEYS.receipt, null);
    if (receipt && receipt.id === id && receipt.fields.length) {
      var panel = make('section', { 'class': 'panel receipt', id: 'applied-panel' });
      panel.appendChild(make('h2', null, 'Saved. The register now holds:'));
      var sheet = make('dl', { 'class': 'sheet' });
      receipt.fields.forEach(function (field) {
        sheet.appendChild(make('dt', null, FIELD_LABEL[field]));
        sheet.appendChild(make('dd', { id: 'applied-' + field.replace(/_/g, '-') }, shown(record, field)));
      });
      panel.appendChild(sheet);
      byId('receipt-slot').appendChild(panel);
    }

    var details = byId('record-sheet');
    details.appendChild(make('dt', null, 'Tag'));
    details.appendChild(make('dd', { 'class': 'tag' }, record.tag));
    FIELD_SEQUENCE.forEach(function (field) {
      details.appendChild(make('dt', null, FIELD_LABEL[field]));
      details.appendChild(make('dd', null, shown(record, field)));
    });

    byId('edit-record').setAttribute('href', 'amend.html?id=' + id);

    var slot = byId('confirm-slot');
    byId('remove-record').addEventListener('click', function () {
      confirmRemoval(slot, record, function () {
        removeRecord(id);
        carryNotice(record.tag + ' was removed from the register.');
        window.location.href = 'equipment.html';
      });
    });

    var history = byId('history-rows');
    var entries = readJournal().filter(function (entry) { return entry.record === id; });
    entries.forEach(function (entry) { history.appendChild(journalRow(entry, false)); });
    if (!entries.length) {
      var blank = make('tr');
      blank.appendChild(make('td', { colspan: '4' }, 'No changes to this record in this session.'));
      history.appendChild(blank);
    }
  }

  function readForm() {
    return {
      asset_name: byId('f-asset-name').value,
      category: byId('f-category').value,
      location: byId('f-location').value,
      custodian: byId('f-custodian').value,
      unitsRaw: byId('f-units').value,
      status: byId('f-status').value,
      notes: byId('f-notes').value
    };
  }

  /* The two rules both forms share. Returns the message to show, or '' when valid. */
  function formProblem(values) {
    if (!values.asset_name.trim()) { return 'Equipment name is required.'; }
    if (!/^\d+$/.test(values.unitsRaw)) { return 'Units held must be a whole number, 0 or more.'; }
    return '';
  }

  function prepareSelects() {
    fillOptions(byId('f-category'), CATEGORY);
    fillOptions(byId('f-location'), LOCATION);
    fillOptions(byId('f-status'), STATUS);
  }

  function amendPage() {
    var stage = byId('stage');
    var id = recordIdFromQuery();
    var record = id === null ? null : findRecord(id);
    var form = byId('amend-form');
    if (!record) {
      form.parentNode.removeChild(form);
      missingRecordPanel(stage);
      return;
    }

    byId('amend-heading').textContent = 'Edit ' + record.tag;
    document.title = 'Edit ' + record.tag + ' — Brasswick Asset Register';
    prepareSelects();
    byId('f-asset-name').value = record.asset_name;
    byId('f-category').value = record.category;
    byId('f-location').value = record.location;
    byId('f-custodian').value = holderOf(record);
    byId('f-units').value = String(record.units);
    byId('f-status').value = record.status;
    byId('f-notes').value = record.notes;
    byId('f-change-reason').value = '';
    byId('discard-edits').setAttribute('href', 'record.html?id=' + id);

    var warning = byId('amend-warning');
    var committed = false;
    byId('save-changes').addEventListener('click', function () {
      if (committed) { return; }
      var values = readForm();
      var reason = byId('f-change-reason').value;
      var problem = formProblem(values);
      if (!problem && !reason.trim()) { problem = 'A reason for the change is required before saving.'; }
      if (problem) { warning.textContent = problem; return; }
      values.units = parseInt(values.unitsRaw, 10);

      var outcome = amendRecord(id, values, reason);
      if (!outcome.ok) {
        warning.textContent = outcome.why === 'unchanged'
          ? 'Nothing on this record has been changed.'
          : 'This record is no longer on the register, so nothing was saved.';
        return;
      }
      committed = true;
      window.location.href = 'record.html?id=' + id;
    });
  }

  function registerPage() {
    prepareSelects();
    byId('f-asset-name').value = '';
    byId('f-category').value = 'instrument';
    byId('f-location').value = 'stores';
    byId('f-custodian').value = '';
    byId('f-units').value = '1';
    byId('f-status').value = 'active';
    byId('f-notes').value = '';

    var warning = byId('register-warning');
    var committed = false;
    byId('create-record').addEventListener('click', function () {
      if (committed) { return; }
      var values = readForm();
      var problem = formProblem(values);
      if (problem) { warning.textContent = problem; return; }
      values.units = parseInt(values.unitsRaw, 10);
      var record = registerRecord(values);
      committed = true;
      carryNotice(record.tag + ' was added to the register.');
      window.location.href = 'record.html?id=' + record.id;
    });
  }

  function changesPage() {
    var rows = byId('journal-rows');
    function render() {
      empty(rows);
      var journal = readJournal();
      journal.forEach(function (entry) { rows.appendChild(journalRow(entry, true)); });
      if (!journal.length) {
        var blank = make('tr');
        blank.appendChild(make('td', { colspan: '5' }, 'The change log is empty.'));
        rows.appendChild(blank);
      }
      byId('journal-count').textContent = journal.length + (journal.length === 1 ? ' entry' : ' entries');
    }
    byId('clear-log').addEventListener('click', function () {
      writeJson(KEYS.journal, []);
      render();
      showNotice('The change log was cleared.');
    });
    render();
  }

  function preferencesPage() {
    function load() {
      var prefs = readPrefs();
      byId('pref-compact').checked = prefs.compact;
      byId('pref-highlight').checked = prefs.highlight;
      byId('pref-accent').value = prefs.accent;
    }
    byId('apply-preferences').addEventListener('click', function () {
      writeJson(KEYS.prefs, {
        compact: byId('pref-compact').checked,
        highlight: byId('pref-highlight').checked,
        accent: byId('pref-accent').value
      });
      applyPrefs();
      showNotice('Preferences applied.');
    });
    byId('restore-defaults').addEventListener('click', function () {
      dropKey(KEYS.prefs);
      applyPrefs();
      load();
      showNotice('Preferences restored to their defaults.');
    });
    load();
  }

  /* ------------------------------------------------------------------- boot */

  var PAGES = {
    overview: overviewPage,
    equipment: equipmentPage,
    locations: locationsPage,
    record: recordPage,
    amend: amendPage,
    register: registerPage,
    changes: changesPage,
    preferences: preferencesPage
  };

  function boot() {
    var page = document.body.getAttribute('data-page');
    applyPrefs();

    /* A receipt describes one save and belongs to the record page it led to. Loading any
       other page retires it, so it is never shown after the operator has moved on. */
    var receipt = readJson(KEYS.receipt, null);
    if (receipt && !(page === 'record' && receipt.id === recordIdFromQuery())) { dropKey(KEYS.receipt); }

    var notice = readJson(KEYS.notice, null);
    if (notice !== null) {
      dropKey(KEYS.notice);
      showNotice(notice);
    }

    if (Object.prototype.hasOwnProperty.call(PAGES, page)) { PAGES[page](); }
  }

  /* A page restored from the back/forward cache would show what it showed when it was
     left rather than what storage holds now; load it afresh instead. */
  window.addEventListener('pageshow', function (event) {
    if (event.persisted) { window.location.reload(); }
  });

  boot();
})();
