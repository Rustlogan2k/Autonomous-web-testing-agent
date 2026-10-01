/* Reelhouse library page: the grid of recordings and every dialog opened over it.
   A dialog's markup is built when it opens and thrown away when it closes; while one is
   open the page behind it is switched off. Which dialog is open is part of the saved
   view, so a reload lands on the same dialog. */
(function () {
  'use strict';

  var Library = Reelhouse.Library;
  var esc = Reelhouse.escapeHtml;
  var view = Reelhouse.View.read();

  var chrome = document.getElementById('chrome');
  var grid = document.getElementById('reel-grid');
  var gridStatus = document.getElementById('grid-status');
  var root = document.getElementById('dialog-root');
  var arrangeBox = document.getElementById('arrange-by');
  var programmeBox = document.getElementById('show-programme');
  var findBox = document.getElementById('find-text');
  var resetButton = document.getElementById('reset-view');
  var addButton = document.getElementById('add-recording');
  var notesButton = document.getElementById('archive-notes');

  var ARRANGE_WORDS = {
    shelf: 'shelf number',
    title: 'title',
    length: 'running time',
    programme: 'programme'
  };

  var EDIT_FIELDS = ['title', 'summary', 'recordedBy', 'programme', 'clearance', 'pinned', 'shareable'];

  var FIELD_NAMES = {
    title: 'Title',
    summary: 'Summary',
    recordedBy: 'Recorded by',
    programme: 'Programme',
    clearance: 'Clearance',
    collection: 'Collection',
    pinned: 'Front shelf',
    shareable: 'Partner stations'
  };

  var FIELD_SLUGS = {
    title: 'title',
    summary: 'summary',
    recordedBy: 'recorded-by',
    programme: 'programme',
    clearance: 'clearance',
    pinned: 'pinned',
    shareable: 'shareable'
  };

  /* Which draft field each form control writes to. */
  var DRAFT_FIELDS = {
    'item-title': 'title',
    'item-summary': 'summary',
    'item-recorded-by': 'recordedBy',
    'item-programme': 'programme',
    'item-clearance': 'clearance',
    'item-pinned': 'pinned',
    'item-shareable': 'shareable',
    'new-title': 'title',
    'new-summary': 'summary',
    'new-recorded-by': 'recordedBy',
    'new-programme': 'programme',
    'new-clearance': 'clearance',
    'new-collection': 'collection',
    'new-pinned': 'pinned',
    'new-shareable': 'shareable'
  };

  /* ---- reading the shelf ------------------------------------------------------------ */

  function visibleRecordings() {
    var wanted = view.find.trim().toLowerCase();
    var list = Library.all().filter(function (record) {
      if (view.programme !== 'all' && record.programme !== view.programme) { return false; }
      if (wanted && Library.searchText(record.id).indexOf(wanted) === -1) { return false; }
      return true;
    });
    var sortKey = null;
    if (view.arrange === 'title') {
      sortKey = function (record) { return record.title.toLowerCase(); };
    } else if (view.arrange === 'length') {
      sortKey = function (record) { return record.seconds; };
    } else if (view.arrange === 'programme') {
      sortKey = function (record) { return Reelhouse.programmeLabel(record.programme).toLowerCase(); };
    }
    if (sortKey) {
      list.sort(function (a, b) {
        var ka = sortKey(a);
        var kb = sortKey(b);
        if (ka < kb) { return -1; }
        if (ka > kb) { return 1; }
        return a.id - b.id;
      });
    }
    return list;
  }

  function shown(field, value) {
    if (field === 'programme') { return Reelhouse.programmeLabel(value); }
    if (field === 'clearance') { return Reelhouse.clearanceLabel(value); }
    if (field === 'collection') { return Reelhouse.collectionLabel(value); }
    if (field === 'pinned') { return value ? 'Kept on the front shelf' : 'Not on the front shelf'; }
    if (field === 'shareable') { return value ? 'Offered to partner stations' : 'Not offered to partner stations'; }
    if (field === 'summary' && value === '') { return '(no summary)'; }
    if (field === 'recordedBy' && value === '') { return '(not credited)'; }
    return value;
  }

  function draftOf(record) {
    var draft = {};
    EDIT_FIELDS.forEach(function (field) { draft[field] = record[field]; });
    return draft;
  }

  function descriptionProblems(draft) {
    var problems = [];
    if (draft.title.trim() === '') { problems.push('Give the recording a title.'); }
    if (draft.title.length > 120) { problems.push('Keep the title to 120 characters or fewer.'); }
    if (draft.summary.length > 400) { problems.push('Keep the summary to 400 characters or fewer.'); }
    if (draft.recordedBy.length > 80) { problems.push('Keep the "Recorded by" line to 80 characters or fewer.'); }
    return problems;
  }

  /* ---- the grid ------------------------------------------------------------------------ */

  function waveform(record) {
    var bars = '';
    for (var i = 0; i < 24; i += 1) {
      var height = ((record.id * 7 + i * 13) % 17) + 6;
      bars += '<rect x="' + (i * 5) + '" y="' + ((30 - height) / 2) + '" width="3" height="' +
        height + '" rx="1"></rect>';
    }
    return '<svg class="reel-wave" viewBox="0 0 120 30" aria-hidden="true">' + bars + '</svg>';
  }

  function card(record, off) {
    return '<li class="reel reel-' + record.programme + '">' +
      waveform(record) +
      '<p class="reel-ref">' + record.ref + ' · ' + Reelhouse.runningTime(record.seconds) + '</p>' +
      '<h2 class="reel-title">' + esc(record.title) + '</h2>' +
      '<p class="reel-meta">' + Reelhouse.programmeLabel(record.programme) + ' · ' +
        Reelhouse.clearanceLabel(record.clearance) + '</p>' +
      '<p class="reel-place">' + Reelhouse.collectionLabel(record.collection) +
        (record.pinned ? ' · front shelf' : '') + '</p>' +
      '<button type="button" class="key reel-open" id="open-item-' + record.id + '"' +
        (off ? ' disabled' : '') + '>Open ' + record.ref + '</button>' +
      '</li>';
  }

  function renderBench(off) {
    var list = visibleRecordings();
    var plain = view.arrange === 'shelf' && view.programme === 'all' && view.find === '';

    arrangeBox.value = view.arrange;
    programmeBox.value = view.programme;
    if (findBox.value !== view.find) { findBox.value = view.find; }

    arrangeBox.disabled = off;
    programmeBox.disabled = off;
    findBox.disabled = off;
    addButton.disabled = off;
    notesButton.disabled = off;
    resetButton.disabled = off || plain;

    var links = chrome.querySelectorAll('a[data-nav]');
    for (var i = 0; i < links.length; i += 1) {
      if (off) {
        links[i].removeAttribute('href');
      } else {
        links[i].setAttribute('href', links[i].getAttribute('data-nav'));
      }
    }
    if (off) {
      chrome.setAttribute('aria-hidden', 'true');
    } else {
      chrome.removeAttribute('aria-hidden');
    }

    gridStatus.textContent = 'Showing ' + list.length + ' of ' + Library.all().length +
      ' recordings, arranged by ' + ARRANGE_WORDS[view.arrange] + '.';
    grid.innerHTML = list.length
      ? list.map(function (record) { return card(record, off); }).join('')
      : '<li class="reels-empty">No recordings match this view.</li>';
  }

  /* ---- dialog building blocks ------------------------------------------------------- */

  function key(id, label, strong, off) {
    return '<button type="button" class="key' + (strong ? ' key-strong' : '') + '" id="' + id + '"' +
      (off ? ' disabled' : '') + '>' + label + '</button>';
  }

  function sheet(kicker, heading, body, keys) {
    return '<div class="veil">' +
      '<section class="sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-heading">' +
      '<p class="sheet-kicker">' + kicker + '</p>' +
      '<h2 id="sheet-heading">' + heading + '</h2>' +
      body +
      '<div class="sheet-keys">' + keys + '</div>' +
      '</section></div>';
  }

  function fact(name, value, id) {
    return '<div class="fact"><dt>' + name + '</dt><dd' + (id ? ' id="' + id + '"' : '') + '>' +
      esc(value) + '</dd></div>';
  }

  function problemList(problems) {
    if (!problems || problems.length === 0) { return ''; }
    return '<ul class="problems" id="sheet-problems">' +
      problems.map(function (text) { return '<li>' + esc(text) + '</li>'; }).join('') + '</ul>';
  }

  function textField(id, name, label, value) {
    return '<div class="field"><label for="' + id + '">' + label + '</label>' +
      '<input type="text" id="' + id + '" name="' + name + '" value="' + esc(value) +
      '" autocomplete="off"></div>';
  }

  function areaField(id, name, label, value) {
    return '<div class="field"><label for="' + id + '">' + label + '</label>' +
      '<textarea id="' + id + '" name="' + name + '" rows="3">\n' + esc(value) + '</textarea></div>';
  }

  function choiceField(id, name, label, options, value) {
    return '<div class="field"><label for="' + id + '">' + label + '</label>' +
      '<select id="' + id + '" name="' + name + '">' +
      options.map(function (option) {
        return '<option value="' + option.value + '"' + (option.value === value ? ' selected' : '') +
          '>' + option.label + '</option>';
      }).join('') +
      '</select></div>';
  }

  function tickField(id, name, label, ticked) {
    return '<div class="field field-tick"><input type="checkbox" id="' + id + '" name="' + name + '"' +
      (ticked ? ' checked' : '') + '><label for="' + id + '">' + label + '</label></div>';
  }

  /* ---- the dialogs --------------------------------------------------------------------- */

  function detailSheet(dlg) {
    var record = Library.byId(dlg.id);
    var count = visibleRecordings().length;
    var body = '<dl class="facts">' +
      fact('Summary', shown('summary', record.summary)) +
      fact('Recorded by', shown('recordedBy', record.recordedBy)) +
      fact('Programme', shown('programme', record.programme)) +
      fact('Clearance', shown('clearance', record.clearance)) +
      fact('Collection', shown('collection', record.collection)) +
      fact('Running time', Reelhouse.runningTime(record.seconds)) +
      fact('Front shelf', shown('pinned', record.pinned)) +
      fact('Partner stations', shown('shareable', record.shareable)) +
      '</dl>';
    var keys =
      key('detail-edit', 'Edit details', true) +
      key('detail-move', 'Move to collection') +
      key('detail-remove', 'Remove recording') +
      key('detail-earlier', 'Earlier in list', false, dlg.slot <= 0) +
      key('detail-later', 'Later in list', false, dlg.slot >= count - 1) +
      key('detail-close', 'Close');
    return sheet(record.ref + ' · recording ' + (dlg.slot + 1) + ' of ' + count + ' in this view',
      esc(record.title), body, keys);
  }

  function editSheet(dlg) {
    var record = Library.byId(dlg.id);
    var draft = dlg.draft;
    var body = problemList(dlg.problems) +
      '<div class="fields">' +
      textField('item-title', 'item_title', 'Title', draft.title) +
      areaField('item-summary', 'item_summary', 'Summary', draft.summary) +
      textField('item-recorded-by', 'recorded_by', 'Recorded by', draft.recordedBy) +
      choiceField('item-programme', 'programme', 'Programme', Reelhouse.PROGRAMMES, draft.programme) +
      choiceField('item-clearance', 'clearance', 'Clearance', Reelhouse.CLEARANCES, draft.clearance) +
      tickField('item-pinned', 'pinned', 'Keep on the front shelf', draft.pinned) +
      tickField('item-shareable', 'shareable', 'Offer to partner stations', draft.shareable) +
      '</div>';
    var keys = key('edit-review', 'Review changes', true) + key('edit-discard', 'Discard edits');
    return sheet(record.ref + ' · editing', 'Edit details', body, keys);
  }

  function reviewSheet(dlg) {
    var record = Library.byId(dlg.id);
    var body = '<p class="sheet-lead">Nothing is saved until you apply these.</p><dl class="facts">' +
      dlg.changed.map(function (field) {
        return '<div class="fact"><dt>' + FIELD_NAMES[field] + '</dt><dd>' +
          '<span class="was">' + esc(shown(field, record[field])) + '</span>' +
          '<span class="becomes"> becomes </span>' +
          '<span class="now">' + esc(shown(field, dlg.draft[field])) + '</span></dd></div>';
      }).join('') + '</dl>';
    var keys = key('review-apply', 'Apply changes', true) + key('review-back', 'Back to editing');
    return sheet(record.ref + ' · ' + dlg.changed.length + ' to apply', 'Review changes', body, keys);
  }

  function receiptSheet(dlg) {
    var record = Library.byId(dlg.id);
    var body = '<p class="sheet-lead">' + record.ref + ' now reads:</p><dl class="facts">' +
      dlg.fields.map(function (field) {
        return fact(FIELD_NAMES[field], shown(field, record[field]), 'receipt-' + FIELD_SLUGS[field]);
      }).join('') + '</dl>';
    return sheet(record.ref + ' · saved', 'Changes applied', body,
      key('receipt-finish', 'Back to the library', true));
  }

  function moveSheet(dlg) {
    var record = Library.byId(dlg.id);
    var body = problemList(dlg.problems) +
      '<p class="sheet-lead">Currently in ' + Reelhouse.collectionLabel(record.collection) + '.</p>' +
      '<div class="fields">' +
      choiceField('move-to', 'move_to', 'Move to', Reelhouse.COLLECTIONS, dlg.choice) +
      '</div>';
    var keys = key('move-apply', 'Move recording', true) + key('move-cancel', 'Leave it here');
    return sheet(record.ref + ' · ' + esc(record.title), 'Move to collection', body, keys);
  }

  function movedSheet(dlg) {
    var record = Library.byId(dlg.id);
    var body = '<p class="sheet-lead">' + esc(record.title) + ' is now in ' +
      Reelhouse.collectionLabel(record.collection) + '.</p>';
    return sheet(record.ref + ' · moved', 'Recording moved', body,
      key('moved-finish', 'Back to the library', true));
  }

  function removeSheet(dlg) {
    var record = Library.byId(dlg.id);
    var body = '<p class="sheet-lead">This takes ' + esc(record.title) +
      ' off the shelf and out of its collection. It cannot be brought back from here.</p>';
    var keys = key('remove-confirm', 'Remove permanently', true) + key('remove-cancel', 'Keep recording');
    return sheet(record.ref + ' · removing', 'Remove this recording?', body, keys);
  }

  function removedSheet(dlg) {
    var body = '<p class="sheet-lead">' + esc(dlg.title) + ' has been removed from the library.</p>';
    return sheet(dlg.ref + ' · removed', 'Recording removed', body,
      key('removed-finish', 'Back to the library', true));
  }

  function addSheet(dlg) {
    var draft = dlg.draft;
    var body;
    var keys;
    if (dlg.step === 1) {
      body = problemList(dlg.problems) + '<div class="fields">' +
        textField('new-title', 'new_title', 'Title', draft.title) +
        areaField('new-summary', 'new_summary', 'Summary', draft.summary) +
        textField('new-recorded-by', 'new_recorded_by', 'Recorded by', draft.recordedBy) +
        '</div>';
      keys = key('add-to-options', 'Go to filing options', true) + key('add-abandon', 'Abandon entry');
      return sheet('New recording · step 1 of 3', 'Describe the recording', body, keys);
    }
    if (dlg.step === 2) {
      body = '<div class="fields">' +
        choiceField('new-programme', 'new_programme', 'Programme', Reelhouse.PROGRAMMES, draft.programme) +
        choiceField('new-clearance', 'new_clearance', 'Clearance', Reelhouse.CLEARANCES, draft.clearance) +
        choiceField('new-collection', 'new_collection', 'File under', Reelhouse.COLLECTIONS, draft.collection) +
        tickField('new-pinned', 'new_pinned', 'Keep on the front shelf', draft.pinned) +
        tickField('new-shareable', 'new_shareable', 'Offer to partner stations', draft.shareable) +
        '</div>';
      keys = key('add-to-check', 'Check the entry', true) +
        key('add-back-describe', 'Back to description') + key('add-abandon', 'Abandon entry');
      return sheet('New recording · step 2 of 3', 'Filing options', body, keys);
    }
    body = '<p class="sheet-lead">This is what will go on the shelf.</p><dl class="facts">' +
      ['title', 'summary', 'recordedBy', 'programme', 'clearance', 'collection', 'pinned', 'shareable']
        .map(function (field) { return fact(FIELD_NAMES[field], shown(field, draft[field])); }).join('') +
      '</dl>';
    keys = key('add-file', 'File recording', true) +
      key('add-back-options', 'Back to filing options') + key('add-abandon', 'Abandon entry');
    return sheet('New recording · step 3 of 3', 'Check the entry', body, keys);
  }

  function addedSheet(dlg) {
    var record = Library.byId(dlg.id);
    var body = '<p class="sheet-lead">Filed as ' + record.ref + '. It will get a running time once the audio is attached.</p>' +
      '<dl class="facts">' +
      ['title', 'programme', 'clearance', 'collection']
        .map(function (field) { return fact(FIELD_NAMES[field], shown(field, record[field])); }).join('') +
      '</dl>';
    return sheet(record.ref + ' · filed', 'Recording filed', body,
      key('added-finish', 'Back to the library', true));
  }

  function notesSheet() {
    var body = '<div class="prose">' +
      '<p>Shelf numbers are given out in the order recordings arrive and are never reused, so a removed recording leaves a gap.</p>' +
      '<p>Running times are measured when the audio is attached, which happens on the studio machines and not in Reelhouse.</p>' +
      '<p>The front shelf is what presenters see first on the studio tablets. Keep it to a handful of recordings.</p>' +
      '<p>Anything marked "Pending clearance" must not go to air until the duty editor has changed it.</p>' +
      '</div>';
    return sheet('For everyone who files audio', 'Archive notes', body, key('notes-close', 'Close'));
  }

  var SHEETS = {
    detail: detailSheet,
    edit: editSheet,
    review: reviewSheet,
    receipt: receiptSheet,
    move: moveSheet,
    moved: movedSheet,
    remove: removeSheet,
    removed: removedSheet,
    add: addSheet,
    added: addedSheet,
    notes: notesSheet
  };

  /* ---- rendering ------------------------------------------------------------------------ */

  function render() {
    var dlg = view.dialog;
    if (dlg && typeof dlg.id === 'number' && !Library.byId(dlg.id)) {
      view.dialog = null;
      dlg = null;
      Reelhouse.View.write(view);
    }
    renderBench(Boolean(dlg));
    root.innerHTML = dlg ? SHEETS[dlg.kind](dlg) : '';
  }

  function show(dialog) {
    view.dialog = dialog;
    Reelhouse.View.write(view);
    render();
  }

  function openDetail(id) {
    var list = visibleRecordings();
    for (var slot = 0; slot < list.length; slot += 1) {
      if (list[slot].id === id) {
        show({ kind: 'detail', id: id, slot: slot });
        return;
      }
    }
  }

  /* ---- what the dialog buttons do ------------------------------------------------------ */

  function reviewEdit(dlg) {
    var record = Library.byId(dlg.id);
    var problems = descriptionProblems(dlg.draft);
    var changed = EDIT_FIELDS.filter(function (field) { return dlg.draft[field] !== record[field]; });
    if (problems.length === 0 && changed.length === 0) {
      problems.push('Nothing has been changed yet.');
    }
    if (problems.length > 0) {
      show({ kind: 'edit', id: dlg.id, slot: dlg.slot, draft: dlg.draft, problems: problems });
      return;
    }
    show({ kind: 'review', id: dlg.id, slot: dlg.slot, draft: dlg.draft, changed: changed });
  }

  function applyEdit(dlg) {
    var patch = {};
    dlg.changed.forEach(function (field) { patch[field] = dlg.draft[field]; });
    var target = Library.byId(dlg.id);
    Library.update(target.id, patch);
    show({ kind: 'receipt', id: dlg.id, fields: dlg.changed });
  }

  function applyMove(dlg) {
    var record = Library.byId(dlg.id);
    if (dlg.choice === record.collection) {
      show({ kind: 'move', id: dlg.id, slot: dlg.slot, choice: dlg.choice,
             problems: ['It is already in that collection.'] });
      return;
    }
    Library.move(dlg.id, dlg.choice);
    show({ kind: 'moved', id: dlg.id });
  }

  function fileRecording(dlg) {
    var record = Library.insert(dlg.draft);
    show({ kind: 'added', id: record.id });
  }

  function step(dlg, delta) {
    var list = visibleRecordings();
    var slot = dlg.slot + delta;
    if (slot < 0 || slot >= list.length) { return; }
    show({ kind: 'detail', id: list[slot].id, slot: slot });
  }

  function press(id) {
    var dlg = view.dialog;
    var record;
    var problems;
    if (!dlg) { return; }
    switch (id) {
      case 'detail-edit':
        show({ kind: 'edit', id: dlg.id, slot: dlg.slot, draft: draftOf(Library.byId(dlg.id)), problems: [] });
        break;
      case 'detail-move':
        show({ kind: 'move', id: dlg.id, slot: dlg.slot, choice: Library.byId(dlg.id).collection, problems: [] });
        break;
      case 'detail-remove':
        show({ kind: 'remove', id: dlg.id, slot: dlg.slot });
        break;
      case 'detail-earlier':
        step(dlg, -1);
        break;
      case 'detail-later':
        step(dlg, 1);
        break;
      case 'edit-review':
        reviewEdit(dlg);
        break;
      case 'edit-discard':
      case 'move-cancel':
      case 'remove-cancel':
        show({ kind: 'detail', id: dlg.id, slot: dlg.slot });
        break;
      case 'review-apply':
        applyEdit(dlg);
        break;
      case 'review-back':
        show({ kind: 'edit', id: dlg.id, slot: dlg.slot, draft: dlg.draft, problems: [] });
        break;
      case 'move-apply':
        applyMove(dlg);
        break;
      case 'remove-confirm':
        record = Library.byId(dlg.id);
        Library.remove(dlg.id);
        show({ kind: 'removed', ref: record.ref, title: record.title });
        break;
      case 'add-to-options':
        problems = descriptionProblems(dlg.draft);
        show({ kind: 'add', step: problems.length ? 1 : 2, draft: dlg.draft, problems: problems });
        break;
      case 'add-to-check':
        show({ kind: 'add', step: 3, draft: dlg.draft, problems: [] });
        break;
      case 'add-back-describe':
        show({ kind: 'add', step: 1, draft: dlg.draft, problems: [] });
        break;
      case 'add-back-options':
        show({ kind: 'add', step: 2, draft: dlg.draft, problems: [] });
        break;
      case 'add-file':
        fileRecording(dlg);
        break;
      case 'detail-close':
      case 'receipt-finish':
      case 'moved-finish':
      case 'removed-finish':
      case 'added-finish':
      case 'add-abandon':
      case 'notes-close':
        show(null);
        break;
      default:
        break;
    }
  }

  function takeDraftValue(event) {
    var control = event.target;
    var dlg = view.dialog;
    if (!dlg) { return; }
    if (control.id === 'move-to') {
      dlg.choice = control.value;
      Reelhouse.View.write(view);
      return;
    }
    var field = DRAFT_FIELDS[control.id];
    if (!field || !dlg.draft) { return; }
    dlg.draft[field] = control.type === 'checkbox' ? control.checked : control.value;
    Reelhouse.View.write(view);
  }

  /* ---- wiring ------------------------------------------------------------------------------ */

  root.addEventListener('click', function (event) {
    var button = event.target.closest('button');
    if (button && !button.disabled) { press(button.id); }
  });
  root.addEventListener('input', takeDraftValue);
  root.addEventListener('change', takeDraftValue);

  grid.addEventListener('click', function (event) {
    var button = event.target.closest('button');
    if (!button || button.disabled || view.dialog) { return; }
    openDetail(Number(button.id.replace('open-item-', '')));
  });

  function changeView(field, value) {
    if (view.dialog) { return; }
    view[field] = value;
    Reelhouse.View.write(view);
    render();
  }

  arrangeBox.addEventListener('change', function () { changeView('arrange', arrangeBox.value); });
  programmeBox.addEventListener('change', function () { changeView('programme', programmeBox.value); });
  findBox.addEventListener('input', function () { changeView('find', findBox.value); });

  resetButton.addEventListener('click', function () {
    if (view.dialog) { return; }
    view = Reelhouse.View.initial();
    Reelhouse.View.write(view);
    render();
  });

  addButton.addEventListener('click', function () {
    if (view.dialog) { return; }
    show({
      kind: 'add',
      step: 1,
      draft: { title: '', summary: '', recordedBy: '', programme: 'news', clearance: 'pending',
               collection: 'unsorted', pinned: false, shareable: false },
      problems: []
    });
  });

  notesButton.addEventListener('click', function () {
    if (view.dialog) { return; }
    show({ kind: 'notes' });
  });

  /* A page brought back from the back/forward cache still holds the shelf as it was when
     the page was left; read it again so the page and the stored shelf agree. */
  window.addEventListener('pageshow', function (event) {
    if (event.persisted) {
      Library.reopen();
      view = Reelhouse.View.read();
      render();
    }
  });

  render();
}());
