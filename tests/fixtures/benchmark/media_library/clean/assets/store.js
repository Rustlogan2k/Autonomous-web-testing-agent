/* Reelhouse: the recordings library of Kestrel FM.
   Shared data layer for every page. The shelf of recordings lives in sessionStorage
   under one key and is mirrored in memory while a page is open; what the library page
   is currently showing (arrangement, filters, the open dialog) lives under a second key,
   so reloading the page brings you back to where you were. */
var Reelhouse = (function () {
  'use strict';

  var RECORDINGS_KEY = 'reelhouse.recordings';
  var VIEW_KEY = 'reelhouse.view';

  var PROGRAMMES = [
    { value: 'news', label: 'News' },
    { value: 'features', label: 'Features' },
    { value: 'music', label: 'Music sessions' },
    { value: 'sport', label: 'Sport' }
  ];

  var CLEARANCES = [
    { value: 'cleared', label: 'Cleared for broadcast' },
    { value: 'station', label: 'Station use only' },
    { value: 'restricted', label: 'Restricted, ask the duty editor' },
    { value: 'pending', label: 'Pending clearance' }
  ];

  var COLLECTIONS = [
    { value: 'unsorted', label: 'Unsorted intake', note: 'Everything that has not been shelved yet.' },
    { value: 'harbour', label: 'Harbour stories', note: 'Voices and sounds from the working waterfront.' },
    { value: 'council', label: 'Council chamber', note: 'Meetings, votes and public questions.' },
    { value: 'sessions', label: 'Live sessions', note: 'Music recorded in Studio B.' }
  ];

  /* Fields that feed the search text of a recording. */
  var INDEXED_FIELDS = ['title', 'summary', 'recordedBy', 'programme'];

  var SEED = [
    { id: 1, ref: 'RH-0101', title: 'Harbour pilots at dawn',
      summary: 'Two pilots talk through bringing a bulk carrier past the breakwater.',
      recordedBy: 'Moira Tanaka', programme: 'features', clearance: 'cleared',
      collection: 'harbour', seconds: 252, pinned: true, shareable: true },
    { id: 2, ref: 'RH-0102', title: 'Council budget vote, full session',
      summary: 'The whole of the February budget meeting, unedited.',
      recordedBy: 'Desk recorder 2', programme: 'news', clearance: 'station',
      collection: 'council', seconds: 3520, pinned: false, shareable: false },
    { id: 3, ref: 'RH-0103', title: 'Ferry strike vox pops',
      summary: 'Commuters at the north slipway on the first morning of the strike.',
      recordedBy: 'Idris Okafor', programme: 'news', clearance: 'pending',
      collection: 'harbour', seconds: 155, pinned: false, shareable: true },
    { id: 4, ref: 'RH-0104', title: 'Saltmarsh birdsong, spring',
      summary: 'Field recording made before sunrise, no narration.',
      recordedBy: 'Moira Tanaka', programme: 'features', clearance: 'cleared',
      collection: 'unsorted', seconds: 663, pinned: false, shareable: true },
    { id: 5, ref: 'RH-0105', title: 'The Lanterns, live in Studio B',
      summary: 'Four songs and a short interview. The band keeps the publishing.',
      recordedBy: 'Studio B desk', programme: 'music', clearance: 'restricted',
      collection: 'sessions', seconds: 1427, pinned: true, shareable: false },
    { id: 6, ref: 'RH-0106', title: 'Derby day commentary highlights',
      summary: 'Goals and the final whistle from the spring derby.',
      recordedBy: 'Pavel Rook', programme: 'sport', clearance: 'station',
      collection: 'unsorted', seconds: 439, pinned: false, shareable: false }
  ];

  function labelIn(list, value) {
    for (var i = 0; i < list.length; i += 1) {
      if (list[i].value === value) { return list[i].label; }
    }
    return value;
  }

  function runningTime(seconds) {
    if (!seconds) { return 'not timed yet'; }
    var minutes = Math.floor(seconds / 60);
    var rest = seconds % 60;
    return (minutes < 10 ? '0' : '') + minutes + ':' + (rest < 10 ? '0' : '') + rest;
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /* ---- the shelf ------------------------------------------------------------------ */

  var shelf = null;
  var searchIndex = {};

  function write() {
    window.sessionStorage.setItem(RECORDINGS_KEY, JSON.stringify(shelf));
  }

  function searchTextOf(record) {
    return [record.title, record.summary, record.recordedBy,
            labelIn(PROGRAMMES, record.programme)].join(' ').toLowerCase();
  }

  function isIndexed(field) {
    return INDEXED_FIELDS.indexOf(field) !== -1;
  }

  function open() {
    var stored = window.sessionStorage.getItem(RECORDINGS_KEY);
    if (stored) {
      shelf = JSON.parse(stored);
    } else {
      shelf = { nextNumber: 107, recordings: JSON.parse(JSON.stringify(SEED)) };
      write();
    }
    searchIndex = {};
    shelf.recordings.forEach(function (record) {
      searchIndex[record.id] = searchTextOf(record);
    });
  }

  function all() {
    return shelf.recordings;
  }

  function byId(id) {
    for (var i = 0; i < shelf.recordings.length; i += 1) {
      if (shelf.recordings[i].id === id) { return shelf.recordings[i]; }
    }
    return null;
  }

  function insert(fields) {
    var number = shelf.nextNumber;
    shelf.nextNumber = number + 1;
    var record = {
      id: number - 100,
      ref: 'RH-0' + number,
      title: fields.title,
      summary: fields.summary,
      recordedBy: fields.recordedBy,
      programme: fields.programme,
      clearance: fields.clearance,
      collection: fields.collection,
      seconds: 0,
      pinned: fields.pinned,
      shareable: fields.shareable
    };
    shelf.recordings.push(record);
    searchIndex[record.id] = searchTextOf(record);
    write();
    return record;
  }

  /* Apply a set of field changes to one recording. Returns the fields that changed. */
  function update(id, patch) {
    var record = byId(id);
    if (!record) { return []; }
    var touched = [];
    Object.keys(patch).forEach(function (field) {
      if (record[field] !== patch[field]) {
        record[field] = patch[field];
        touched.push(field);
      }
    });
    if (touched.some(isIndexed)) { searchIndex[record.id] = searchTextOf(record); }
    if (touched.length > 0) { write(); }
    return touched;
  }

  function move(id, collection) {
    var record = byId(id);
    if (!record) { return; }
    record.collection = collection;
    write();
  }

  function remove(id) {
    shelf.recordings = shelf.recordings.filter(function (record) { return record.id !== id; });
    delete searchIndex[id];
    write();
  }

  function searchText(id) {
    return searchIndex[id] || '';
  }

  /* ---- what the library page is showing --------------------------------------------- */

  function defaultView() {
    return { arrange: 'shelf', programme: 'all', find: '', dialog: null };
  }

  function readView() {
    var stored = window.sessionStorage.getItem(VIEW_KEY);
    return stored ? JSON.parse(stored) : defaultView();
  }

  function writeView(view) {
    window.sessionStorage.setItem(VIEW_KEY, JSON.stringify(view));
  }

  open();

  return {
    PROGRAMMES: PROGRAMMES,
    CLEARANCES: CLEARANCES,
    COLLECTIONS: COLLECTIONS,
    programmeLabel: function (value) { return labelIn(PROGRAMMES, value); },
    clearanceLabel: function (value) { return labelIn(CLEARANCES, value); },
    collectionLabel: function (value) { return labelIn(COLLECTIONS, value); },
    runningTime: runningTime,
    escapeHtml: escapeHtml,
    Library: {
      reopen: open,
      all: all,
      byId: byId,
      insert: insert,
      update: update,
      move: move,
      remove: remove,
      searchText: searchText
    },
    View: {
      initial: defaultView,
      read: readView,
      write: writeView
    }
  };
}());
