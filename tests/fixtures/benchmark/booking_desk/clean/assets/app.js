/*
 * Millrace Rooms hire desk: a client-rendered single-page application.
 *
 * Every screen is a view built into <main id="view"> when it is shown and torn down when
 * it is left; nothing is kept in the document hidden. The URL never changes.
 *
 * All state lives in one object persisted to sessionStorage after every change, so a
 * reload lands on the same view with the same draft. Form fields write through to the
 * draft on every input event, so the draft always holds what the person last entered;
 * the stage gates validate the draft, never the DOM.
 *
 * Money is held in pence (integers) and formatted only for display.
 */
(function () {
  'use strict';

  var STORE_KEY = 'millrace.desk.v1';
  var POUND = '£';
  var DASH = '—';
  var CLOSING_HOUR = 22;
  var COMMUNITY_RATE = 0.2;
  var BOOKABLE_YEARS = ['2026', '2027'];

  var STATUS = { provisional: 'Provisional', reserved: 'Reserved', released: 'Released' };

  var ROOMS = [
    { id: 'long-gallery', name: 'Long Gallery', seats: 80, rate: 6400, stepFree: true,
      where: 'First floor, lift access',
      blurb: 'The old spinning floor: forty metres of oak boards under a run of north lights.',
      kit: 'Hearing loop, blackout blinds, sprung floor' },
    { id: 'weaving-shed', name: 'Weaving Shed', seats: 60, rate: 5200, stepFree: true,
      where: 'Ground floor, yard entrance',
      blurb: 'Saw-tooth roof, whitewashed brick and doors that open onto the yard.',
      kit: 'Hearing loop, catering servery next door' },
    { id: 'dye-house', name: 'Dye House', seats: 45, rate: 4000, stepFree: false,
      where: 'Lower yard, four steps down',
      blurb: 'Stone vats turned into planters and a long table that seats the whole room.',
      kit: 'Wood burner in winter, garden access' },
    { id: 'counting-room', name: 'Counting Room', seats: 14, rate: 2400, stepFree: true,
      where: 'Ground floor, main building',
      blurb: 'The mill office, panelled and quiet: made for board meetings and interviews.',
      kit: 'Wall screen, conference phone' },
    { id: 'snug', name: 'The Snug', seats: 6, rate: 1500, stepFree: false,
      where: 'Mezzanine, narrow stair',
      blurb: 'A small round table above the millrace, with the sound of the water below.',
      kit: 'Whiteboard wall' }
  ];

  var ARRIVALS = [
    { value: '9', label: '09:00' },
    { value: '12', label: '12:00' },
    { value: '15', label: '15:00' },
    { value: '18', label: '18:00' }
  ];

  var LENGTHS = [
    { value: '2', label: '2 hours' },
    { value: '3', label: '3 hours' },
    { value: '4', label: 'Half day (4 hours)' },
    { value: '8', label: 'Full day (8 hours)' }
  ];

  var LAYOUTS = [
    { value: 'theatre', label: 'Theatre rows' },
    { value: 'boardroom', label: 'Boardroom table' },
    { value: 'cabaret', label: 'Cabaret tables' },
    { value: 'standing', label: 'Standing reception' }
  ];

  var CATERING = [
    { value: '0', label: 'No catering', name: 'No catering' },
    { value: '4', label: 'Tea, coffee and biscuits (' + POUND + '4.00 a guest)', name: 'Tea, coffee and biscuits' },
    { value: '9', label: 'Sandwich lunch (' + POUND + '9.00 a guest)', name: 'Sandwich lunch' },
    { value: '14', label: 'Hot buffet (' + POUND + '14.00 a guest)', name: 'Hot buffet' }
  ];

  var EXTRAS = [
    { key: 'projector', label: 'Projector and screen', price: 2500 },
    { key: 'sound', label: 'PA system with two microphones', price: 3500 },
    { key: 'flipchart', label: 'Flipchart pack', price: 800 },
    { key: 'early', label: 'Early access, one hour to set up', price: 2000 }
  ];

  var SETTLE = [
    { value: 'desk', label: 'Pay by card at the desk on the day' },
    { value: 'invoice', label: 'Invoice my organisation' }
  ];

  var STAGES = [
    { key: 'session', label: '1. Session' },
    { key: 'extras', label: '2. Catering and extras' },
    { key: 'details', label: '3. Your details' },
    { key: 'review', label: '4. Review' }
  ];

  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
    'September', 'October', 'November', 'December'];

  // ---------------------------------------------------------------------------------
  // state
  // ---------------------------------------------------------------------------------

  function freshState() {
    return { view: 'rooms', ui: {}, draft: null, reservations: [], seq: 0, lastRef: null };
  }

  function freshDraft(roomId) {
    return {
      room: roomId,
      date: '',
      arrival: '9',
      hours: '2',
      layout: 'theatre',
      guests: '',
      catering: '0',
      extras: {},
      name: '',
      email: '',
      phone: '',
      organisation: '',
      notes: '',
      settle: 'desk',
      community: false,
      stage: 'session',
      reached: 0,
      status: STATUS.provisional
    };
  }

  function load() {
    try {
      var raw = window.sessionStorage.getItem(STORE_KEY);
      if (raw) {
        var parsed = JSON.parse(raw);
        if (parsed && typeof parsed === 'object' && parsed.view) return parsed;
      }
    } catch (ignored) {
      // Storage unavailable or corrupt: begin afresh in memory.
    }
    return freshState();
  }

  function save() {
    try {
      window.sessionStorage.setItem(STORE_KEY, JSON.stringify(state));
    } catch (ignored) {
      // Storage unavailable: the desk keeps working for this page's lifetime.
    }
  }

  var state = load();

  function go(view, ui) {
    state.view = view;
    state.ui = ui || {};
    save();
    render();
  }

  function refresh() {
    save();
    render();
  }

  // ---------------------------------------------------------------------------------
  // lookups and formatting
  // ---------------------------------------------------------------------------------

  function roomById(id) {
    for (var i = 0; i < ROOMS.length; i++) if (ROOMS[i].id === id) return ROOMS[i];
    return null;
  }

  function optionFor(list, value) {
    for (var i = 0; i < list.length; i++) if (list[i].value === value) return list[i];
    return list[0];
  }

  function whole(value) {
    var n = parseInt(value, 10);
    return isNaN(n) ? 0 : n;
  }

  function money(pence) {
    return (pence / 100).toFixed(2);
  }

  function clock(hour) {
    return (hour < 10 ? '0' : '') + hour + ':00';
  }

  function longDate(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
    if (!m) return iso || DASH;
    return parseInt(m[3], 10) + ' ' + MONTHS[parseInt(m[2], 10) - 1] + ' ' + m[1];
  }

  function stageIndex(key) {
    for (var i = 0; i < STAGES.length; i++) if (STAGES[i].key === key) return i;
    return 0;
  }

  function sessionLine(d) {
    var arrival = whole(d.arrival);
    return longDate(d.date) + ', ' + clock(arrival) + ' to ' + clock(arrival + whole(d.hours));
  }

  function chosenExtras(d) {
    return EXTRAS.filter(function (x) { return !!(d.extras && d.extras[x.key]); });
  }

  // ---------------------------------------------------------------------------------
  // pricing
  // ---------------------------------------------------------------------------------

  function quote(d) {
    var room = roomById(d.room);
    var hours = whole(d.hours);
    var covers = whole(d.guests);
    var perHead = whole(d.catering) * 100;
    var hire = room.rate * hours;
    // The community rate is a reduction on room hire only (see the hire terms).
    var concession = d.community ? Math.round(hire * COMMUNITY_RATE) : 0;
    var catering = covers * perHead;
    var extras = chosenExtras(d).reduce(function (sum, x) { return sum + x.price; }, 0);
    return {
      hire: hire,
      concession: concession,
      catering: catering,
      extras: extras,
      total: hire - concession + catering + extras
    };
  }

  // ---------------------------------------------------------------------------------
  // stage gates
  // ---------------------------------------------------------------------------------

  function checkSession(d) {
    var problems = [];
    var room = roomById(d.room);
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d.date || '');
    if (!m) {
      problems.push('Choose the date of your session.');
    } else if (BOOKABLE_YEARS.indexOf(m[1]) < 0) {
      problems.push('The desk is taking reservations for dates in 2026 and 2027 only.');
    }
    if (whole(d.arrival) + whole(d.hours) > CLOSING_HOUR) {
      problems.push('The building closes at 22:00, so that session would run too late. ' +
        'Pick an earlier arrival or a shorter session.');
    }
    if (!/^\d+$/.test(d.guests || '')) {
      problems.push('Tell us how many guests are coming.');
    } else {
      var guests = parseInt(d.guests, 10);
      if (guests < 1) {
        problems.push('A reservation needs at least one guest.');
      } else if (guests > room.seats) {
        problems.push(room.name + ' holds ' + room.seats + ' guests at most. ' +
          'Lower the number or choose a larger room.');
      }
    }
    return problems;
  }

  function checkExtras() {
    return [];
  }

  function checkDetails(d) {
    var problems = [];
    var name = (d.name || '').trim();
    if (name.length < 2) problems.push('Give the name of the person making the reservation.');
    else if (name.length > 80) problems.push('Keep the name to 80 characters or fewer.');
    var email = (d.email || '').trim();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) problems.push('Give an email address we can reach you on.');
    else if (email.length > 120) problems.push('That email address is too long for our records (120 characters at most).');
    var phone = (d.phone || '').trim();
    if (phone && !/^\+?[0-9][0-9 \-]{5,19}$/.test(phone)) {
      problems.push('The phone number should be 6 to 20 digits; leave it blank if you prefer email.');
    }
    if ((d.organisation || '').length > 80) problems.push('Keep the organisation name to 80 characters or fewer.');
    if ((d.notes || '').length > 600) problems.push('Keep notes for the desk to 600 characters or fewer.');
    return problems;
  }

  var GATES = { session: checkSession, extras: checkExtras, details: checkDetails };

  /** Enter a stage of the reservation, first proving every stage before it is complete. */
  function enterStage(key) {
    var d = state.draft;
    if (!d) return go('rooms');
    var target = stageIndex(key);
    for (var i = 0; i < target; i++) {
      var problems = GATES[STAGES[i].key](d);
      if (problems.length) {
        d.stage = STAGES[i].key;
        return go('flow', { problems: problems });
      }
    }
    d.stage = key;
    if (target > d.reached) d.reached = target;
    go('flow');
  }

  function stageComplete(d) {
    for (var i = 0; i < STAGES.length - 1; i++) {
      if (GATES[STAGES[i].key](d).length) return false;
    }
    return true;
  }

  // ---------------------------------------------------------------------------------
  // commands
  // ---------------------------------------------------------------------------------

  function chooseRoom(roomId) {
    state.draft = freshDraft(roomId);
    go('flow');
  }

  function finalise() {
    var d = state.draft;
    if (!d || !stageComplete(d)) return enterStage('review');
    state.seq += 1;
    var record = JSON.parse(JSON.stringify(d));
    record.ref = 'MR-' + (1000 + state.seq);
    record.costs = quote(d);
    record.status = STATUS.reserved;
    state.reservations.push(record);
    state.lastRef = record.ref;
    state.draft = null;
    go('reserved');
  }

  function release(ref) {
    state.reservations.forEach(function (r) {
      if (r.ref === ref) r.status = STATUS.released;
    });
    go('reservations', { notice: 'Reservation ' + ref + ' has been released.' });
  }

  function discardDraft() {
    state.draft = null;
    go('rooms', { notice: 'Your reservation in progress was discarded.' });
  }

  // ---------------------------------------------------------------------------------
  // DOM helpers
  // ---------------------------------------------------------------------------------

  function el(tag, attrs, kids) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value === false || value === null || value === undefined) return;
        if (key === 'text') node.textContent = value;
        else if (key === 'on') {
          Object.keys(value).forEach(function (evt) { node.addEventListener(evt, value[evt]); });
        } else if (value === true) node.setAttribute(key, '');
        else node.setAttribute(key, String(value));
      });
    }
    (kids || []).forEach(function (kid) {
      if (kid === null || kid === undefined || kid === false) return;
      node.appendChild(typeof kid === 'string' ? document.createTextNode(kid) : kid);
    });
    return node;
  }

  function button(id, label, onClick, variant) {
    return el('button', {
      type: 'button', id: id, 'class': 'btn' + (variant ? ' btn-' + variant : ''),
      text: label, on: { click: onClick }
    });
  }

  function actions(buttons) {
    return el('div', { 'class': 'actions' }, buttons);
  }

  function problemsBox(problems) {
    if (!problems || !problems.length) return null;
    return el('div', { 'class': 'problems', role: 'alert' }, [
      el('p', { text: 'Before you go on:' }),
      el('ul', null, problems.map(function (p) { return el('li', { text: p }); }))
    ]);
  }

  function noticeBox(text) {
    return text ? el('p', { 'class': 'note', role: 'status', text: text }) : null;
  }

  /** A labelled control whose every change is written straight into `target[key]`. */
  function bound(target, key, control) {
    function write() {
      target[key] = control.value;
      save();
    }
    control.addEventListener('input', write);
    control.addEventListener('change', write);
    return control;
  }

  function inputField(opts) {
    var control = el('input', {
      type: opts.type, id: opts.id, name: opts.name, value: opts.value || '',
      min: opts.min, step: opts.step, autocomplete: opts.autocomplete || 'off'
    });
    if (opts.target) bound(opts.target, opts.key, control);
    return el('div', { 'class': 'field' + (opts.wide ? ' field-wide' : '') }, [
      el('label', { 'for': opts.id, text: opts.label }),
      control,
      opts.hint ? el('span', { 'class': 'hint', text: opts.hint }) : null
    ]);
  }

  function selectField(opts) {
    var control = el('select', { id: opts.id, name: opts.name },
      opts.options.map(function (o) {
        return el('option', { value: o.value, selected: o.value === opts.value, text: o.label });
      }));
    if (opts.target) bound(opts.target, opts.key, control);
    if (opts.onChange) control.addEventListener('change', opts.onChange);
    return el('div', { 'class': 'field' + (opts.wide ? ' field-wide' : '') }, [
      el('label', { 'for': opts.id, text: opts.label }),
      control,
      opts.hint ? el('span', { 'class': 'hint', text: opts.hint }) : null
    ]);
  }

  function textareaField(opts) {
    var control = el('textarea', { id: opts.id, name: opts.name, rows: '4', text: opts.value || '' });
    if (opts.target) bound(opts.target, opts.key, control);
    return el('div', { 'class': 'field field-wide' }, [
      el('label', { 'for': opts.id, text: opts.label }),
      control,
      opts.hint ? el('span', { 'class': 'hint', text: opts.hint }) : null
    ]);
  }

  function tick(opts) {
    var box = el('input', { type: 'checkbox', id: opts.id, name: opts.name, checked: !!opts.checked });
    box.addEventListener('change', function () { opts.onChange(box.checked); });
    return el('li', null, [box, el('label', { 'for': opts.id, text: opts.label })]);
  }

  function formShell(kids) {
    var form = el('form', { novalidate: true, autocomplete: 'off' }, kids);
    form.addEventListener('submit', function (event) { event.preventDefault(); });
    return form;
  }

  function facts(pairs) {
    var list = el('dl', { 'class': 'facts' });
    pairs.forEach(function (pair) {
      if (!pair) return;
      list.appendChild(el('dt', { text: pair[0] }));
      list.appendChild(el('dd', { text: pair[1] }));
    });
    return list;
  }

  // ---------------------------------------------------------------------------------
  // views: rooms
  // ---------------------------------------------------------------------------------

  var ROOM_FILTERS = [
    { value: 'all', label: 'All rooms' },
    { value: 'large', label: 'Seats 40 or more' },
    { value: 'small', label: 'Seats under 20' },
    { value: 'stepfree', label: 'Step-free access' }
  ];

  function roomMatches(room, filter) {
    if (filter === 'large') return room.seats >= 40;
    if (filter === 'small') return room.seats < 20;
    if (filter === 'stepfree') return room.stepFree;
    return true;
  }

  function viewRooms() {
    var filter = state.ui.filter || 'all';
    var d = state.draft;
    var resume = null;
    if (d) {
      var room = roomById(d.room);
      resume = el('section', { 'class': 'panel', 'aria-label': 'Reservation in progress' }, [
        el('h2', { text: 'Reservation in progress' }),
        el('p', { text: room.name + ', at the ' + STAGES[stageIndex(d.stage)].label.replace(/^\d\.\s*/, '').toLowerCase() +
          ' stage. Choosing another room below replaces it.' }),
        actions([
          button('resume-draft', 'Resume reservation', function () { enterStage(state.draft.stage); }, 'main'),
          button('discard-draft', 'Discard it', discardDraft)
        ])
      ]);
    }
    var cards = ROOMS.filter(function (r) { return roomMatches(r, filter); }).map(function (r) {
      return el('li', { 'class': 'room-card' }, [
        el('h2', { text: r.name }),
        el('p', { text: r.blurb }),
        el('p', { 'class': 'room-facts', text: 'Seats up to ' + r.seats + ' ' + DASH + ' ' + POUND +
          money(r.rate) + ' an hour ' + DASH + ' ' + r.where }),
        button('choose-' + r.id, 'Choose ' + r.name, function () { chooseRoom(r.id); }, 'main')
      ]);
    });
    return el('section', null, [
      el('h1', { text: 'Rooms to hire' }),
      el('p', { 'class': 'lede', text: 'Five rooms in a restored cotton mill, hired by the hour for ' +
        'meetings, workshops, rehearsals and celebrations. Choose a room to check your date and price it up.' }),
      noticeBox(state.ui.notice),
      resume,
      formShell([
        el('div', { 'class': 'fields' }, [
          selectField({ id: 'room-filter', name: 'room_filter', label: 'Show', options: ROOM_FILTERS,
            value: filter, onChange: function (e) { state.ui.filter = e.target.value; refresh(); } })
        ])
      ]),
      cards.length ? el('ul', { 'class': 'room-list', style: 'margin-top:1rem' }, cards)
        : el('p', { text: 'No rooms match that filter.' })
    ]);
  }

  // ---------------------------------------------------------------------------------
  // views: the reservation flow
  // ---------------------------------------------------------------------------------

  function stageRail(d) {
    return el('ol', { 'class': 'stage-rail', 'aria-label': 'Reservation stages' }, STAGES.map(function (s, i) {
      return el('li', null, [el('button', {
        type: 'button', id: 'rail-' + s.key, text: s.label,
        disabled: i > d.reached,
        'aria-current': s.key === d.stage ? 'step' : null,
        on: { click: function () { enterStage(s.key); } }
      })]);
    }));
  }

  function discardPanel() {
    return el('section', { 'class': 'panel', role: 'alertdialog', 'aria-label': 'Discard reservation' }, [
      el('h2', { text: 'Discard this reservation?' }),
      el('p', { text: 'Everything you have entered for this room will be cleared.' }),
      actions([
        button('discard-yes', 'Yes, discard it', discardDraft, 'main'),
        button('discard-no', 'No, keep it', function () { state.ui = {}; refresh(); })
      ])
    ]);
  }

  function commonButtons(d) {
    var list = [];
    if (d.reached >= 3 && d.stage !== 'review') {
      list.push(button('back-to-review', 'Return to review', function () { enterStage('review'); }));
    }
    list.push(button('save-later', 'Save for later', function () {
      go('rooms', { notice: 'Saved. Your reservation will wait here until you close this tab.' });
    }));
    list.push(button('cancel-draft', 'Cancel reservation', function () {
      state.ui = { discarding: true };
      refresh();
    }, 'plain'));
    return list;
  }

  function stageSession(d) {
    var room = roomById(d.room);
    return [
      el('h1', { text: 'When do you need ' + room.name + '?' }),
      formShell([
        el('div', { 'class': 'fields' }, [
          inputField({ type: 'date', id: 'session-date', name: 'session_date', label: 'Date',
            value: d.date, target: d, key: 'date', hint: 'We take reservations for 2026 and 2027.' }),
          selectField({ id: 'arrival', name: 'arrival', label: 'Arrival time', options: ARRIVALS,
            value: d.arrival, target: d, key: 'arrival' }),
          selectField({ id: 'hours', name: 'hours', label: 'Length of hire', options: LENGTHS,
            value: d.hours, target: d, key: 'hours', hint: 'The building closes at 22:00.' }),
          selectField({ id: 'layout', name: 'layout', label: 'Room layout', options: LAYOUTS,
            value: d.layout, target: d, key: 'layout' }),
          inputField({ type: 'number', id: 'guests', name: 'guests', label: 'Number of guests',
            value: d.guests, target: d, key: 'guests', min: '1', step: '1',
            hint: room.name + ' holds up to ' + room.seats + '.' })
        ])
      ]),
      actions([
        button('to-extras', 'On to catering and extras', function () { enterStage('extras'); }, 'main'),
        button('change-room', 'Choose a different room', function () { go('rooms'); }),
        button('clear-session', 'Clear these details', function () {
          d.date = ''; d.arrival = '9'; d.hours = '2'; d.layout = 'theatre'; d.guests = '';
          state.ui = {};
          refresh();
        })
      ].concat(commonButtons(d)))
    ];
  }

  function stageExtras(d) {
    return [
      el('h1', { text: 'Catering and extras' }),
      el('p', { 'class': 'lede', text: 'Catering is charged for every guest. Extras are a flat charge for the session.' }),
      formShell([
        el('div', { 'class': 'fields' }, [
          selectField({ id: 'catering', name: 'catering', label: 'Catering', options: CATERING,
            value: d.catering, target: d, key: 'catering', wide: true,
            hint: 'Served by Weir Kitchen, our tenants in the yard.' })
        ]),
        el('fieldset', null, [
          el('legend', { text: 'Equipment and access' }),
          el('ul', { 'class': 'ticks' }, EXTRAS.map(function (x) {
            return tick({ id: 'extra-' + x.key, name: 'extra_' + x.key, checked: !!d.extras[x.key],
              label: x.label + ' (' + POUND + money(x.price) + ')',
              onChange: function (on) { d.extras[x.key] = on; save(); } });
          }))
        ])
      ]),
      actions([
        button('to-details', 'On to your details', function () { enterStage('details'); }, 'main'),
        button('back-session', 'Back to session details', function () { enterStage('session'); }),
        button('clear-extras', 'Clear catering and extras', function () {
          d.catering = '0'; d.extras = {};
          state.ui = {};
          refresh();
        })
      ].concat(commonButtons(d)))
    ];
  }

  function stageDetails(d) {
    return [
      el('h1', { text: 'Your details' }),
      el('p', { 'class': 'lede', text: 'We send the reservation to this address and call only if something changes on the day.' }),
      formShell([
        el('div', { 'class': 'fields' }, [
          inputField({ type: 'text', id: 'contact-name', name: 'contact_name', label: 'Your name',
            value: d.name, target: d, key: 'name', autocomplete: 'name' }),
          inputField({ type: 'email', id: 'contact-email', name: 'contact_email', label: 'Email address',
            value: d.email, target: d, key: 'email', autocomplete: 'email' }),
          inputField({ type: 'tel', id: 'contact-phone', name: 'contact_phone', label: 'Phone (optional)',
            value: d.phone, target: d, key: 'phone', autocomplete: 'tel' }),
          inputField({ type: 'text', id: 'organisation', name: 'organisation', label: 'Organisation (optional)',
            value: d.organisation, target: d, key: 'organisation' }),
          textareaField({ id: 'desk-notes', name: 'desk_notes', label: 'Notes for the desk (optional)',
            value: d.notes, target: d, key: 'notes', hint: 'Dietary needs, access, deliveries.' })
        ]),
        el('fieldset', null, [
          el('legend', { text: 'Settling up' }),
          el('ul', { 'class': 'ticks' }, SETTLE.map(function (s) {
            var radio = el('input', { type: 'radio', id: 'settle-' + s.value, name: 'settle', value: s.value,
              checked: d.settle === s.value });
            radio.addEventListener('change', function () { if (radio.checked) { d.settle = s.value; save(); } });
            return el('li', null, [radio, el('label', { 'for': 'settle-' + s.value, text: s.label })]);
          }))
        ]),
        el('fieldset', null, [
          el('legend', { text: 'Rates' }),
          el('ul', { 'class': 'ticks' }, [
            tick({ id: 'community-rate', name: 'community_rate', checked: d.community,
              label: 'We are a registered charity or community group (20% off room hire)',
              onChange: function (on) { d.community = on; save(); } })
          ])
        ])
      ]),
      actions([
        button('to-review', 'Review reservation', function () { enterStage('review'); }, 'main'),
        button('back-extras', 'Back to catering and extras', function () { enterStage('extras'); }),
        button('clear-details', 'Clear my details', function () {
          d.name = ''; d.email = ''; d.phone = ''; d.organisation = ''; d.notes = '';
          d.settle = 'desk'; d.community = false;
          state.ui = {};
          refresh();
        })
      ].concat(commonButtons(d)))
    ];
  }

  function chargeRow(label, detail, id, pence, sign) {
    return el('tr', null, [
      el('th', { scope: 'row', text: label }),
      el('td', { text: detail }),
      el('td', { 'class': 'amount' }, [(sign || '') + POUND, el('span', { id: id, text: money(pence) })])
    ]);
  }

  function stageReview(d) {
    var room = roomById(d.room);
    var costs = quote(d);
    var cater = optionFor(CATERING, d.catering);
    var extras = chosenExtras(d);
    var rows = [
      chargeRow('Room hire', d.hours + ' hours at ' + POUND + money(room.rate) + ' an hour', 'review-hire', costs.hire)
    ];
    if (costs.concession) {
      rows.push(chargeRow('Community rate', '20% off room hire', 'review-concession', costs.concession, '- '));
    }
    if (whole(d.catering) > 0) {
      rows.push(chargeRow('Catering', cater.name + ' for ' + whole(d.guests) + ' guests at ' + POUND +
        money(whole(d.catering) * 100) + ' each', 'review-catering', costs.catering));
    }
    rows.push(chargeRow('Extras', extras.length ? extras.map(function (x) { return x.label; }).join(', ') : 'None chosen',
      'review-extras', costs.extras));
    rows.push(el('tr', { 'class': 'total-row' }, [
      el('th', { scope: 'row', text: 'Total due' }),
      el('td', { text: optionFor(SETTLE, d.settle).label }),
      el('td', { 'class': 'amount' }, [POUND, el('span', { id: 'review-total', text: money(costs.total) })])
    ]));

    return [
      el('h1', { text: 'Review your reservation' }),
      el('p', { 'class': 'lede', text: 'Nothing is held until you finalise. Status: ' + d.status + '.' }),
      el('section', { 'class': 'panel' }, [
        el('h2', { text: room.name }),
        facts([
          ['When', sessionLine(d)],
          ['Layout', optionFor(LAYOUTS, d.layout).label],
          ['Guests', String(whole(d.guests))],
          ['Name', d.name.trim()],
          ['Email', d.email.trim()],
          d.phone.trim() ? ['Phone', d.phone.trim()] : null,
          d.organisation.trim() ? ['Organisation', d.organisation.trim()] : null,
          d.notes.trim() ? ['Notes', d.notes.trim()] : null
        ])
      ]),
      el('section', { 'class': 'panel' }, [
        el('h2', { text: 'Charges' }),
        el('table', { 'class': 'charges' }, [el('tbody', null, rows)])
      ]),
      actions([
        button('finalise', 'Finalise reservation', finalise, 'main'),
        button('amend-session', 'Amend session', function () { enterStage('session'); }),
        button('amend-extras', 'Amend catering and extras', function () { enterStage('extras'); }),
        button('amend-details', 'Amend your details', function () { enterStage('details'); })
      ].concat(commonButtons(d)))
    ];
  }

  var STAGE_VIEWS = { session: stageSession, extras: stageExtras, details: stageDetails, review: stageReview };

  function viewFlow() {
    var d = state.draft;
    var room = roomById(d.room);
    return el('section', null, [
      stageRail(d),
      el('p', { 'class': 'room-strip', text: 'Reserving ' + room.name + ' ' + DASH + ' seats up to ' +
        room.seats + ' ' + DASH + ' ' + POUND + money(room.rate) + ' an hour' }),
      problemsBox(state.ui.problems),
      state.ui.discarding ? discardPanel() : null
    ].concat(STAGE_VIEWS[d.stage](d)));
  }

  // ---------------------------------------------------------------------------------
  // views: after finalising, and the session's reservations
  // ---------------------------------------------------------------------------------

  function findReservation(ref) {
    for (var i = 0; i < state.reservations.length; i++) {
      if (state.reservations[i].ref === ref) return state.reservations[i];
    }
    return null;
  }

  function viewReserved() {
    var r = findReservation(state.lastRef);
    var room = roomById(r.room);
    return el('section', null, [
      el('h1', { text: 'Thank you, ' + r.name.trim() }),
      el('p', { 'class': 'lede', text: 'Your reference is ' + r.ref + '. A copy is on its way to ' + r.email.trim() + '.' }),
      el('section', { 'class': 'panel' }, [
        el('h2', { text: room.name }),
        el('p', null, ['Status: ', el('strong', { id: 'reserved-status', text: r.status })]),
        facts([
          ['Reference', r.ref],
          ['When', sessionLine(r)],
          ['Guests', String(whole(r.guests))],
          ['Settling up', optionFor(SETTLE, r.settle).label],
          ['Total due', POUND + money(r.costs.total)]
        ])
      ]),
      actions([
        button('see-reservations', 'See my reservations', function () { go('reservations'); }, 'main'),
        button('another-room', 'Reserve another room', function () { go('rooms'); })
      ])
    ]);
  }

  function viewReservations() {
    var items = state.reservations.map(function (r) {
      var room = roomById(r.room);
      var controls = [];
      if (r.status !== STATUS.released) {
        if (state.ui.releasing === r.ref) {
          controls.push(el('div', { 'class': 'problems', role: 'alertdialog' }, [
            el('p', { text: 'Release ' + r.ref + '? The room goes back on sale and this cannot be undone.' }),
            actions([
              button('release-yes', 'Yes, release it', function () { release(r.ref); }, 'main'),
              button('release-no', 'Keep it', function () { state.ui = {}; refresh(); })
            ])
          ]));
        } else {
          controls.push(actions([button('release-' + r.ref, 'Release ' + r.ref, function () {
            state.ui = { releasing: r.ref };
            refresh();
          })]));
        }
      }
      return el('li', { 'class': 'panel' }, [
        el('h2', null, [r.ref + ' ' + DASH + ' ' + room.name + ' ', el('span', { 'class': 'badge res-status', text: r.status })]),
        facts([
          ['When', sessionLine(r)],
          ['Guests', String(whole(r.guests))],
          ['Total due', POUND + money(r.costs.total)]
        ])
      ].concat(controls));
    });
    return el('section', null, [
      el('h1', { text: 'My reservations' }),
      el('p', { 'class': 'lede', text: 'Reservations made in this browser tab.' }),
      noticeBox(state.ui.notice),
      items.length ? el('ul', { 'class': 'res-list' }, items)
        : el('p', { text: 'You have not reserved a room yet.' }),
      actions([button('browse-rooms', 'Browse rooms', function () { go('rooms'); })])
    ]);
  }

  // ---------------------------------------------------------------------------------
  // views: information
  // ---------------------------------------------------------------------------------

  function topicList(prefix, topics) {
    return el('ul', { 'class': 'topic-list' }, topics.map(function (t) {
      var open = state.ui.open === t.key;
      return el('li', null, [
        el('button', {
          type: 'button', id: prefix + '-' + t.key, 'class': 'btn btn-plain', text: t.title,
          'aria-expanded': open ? 'true' : 'false',
          on: { click: function () { state.ui.open = open ? null : t.key; refresh(); } }
        }),
        open ? el('p', { text: t.body }) : null
      ]);
    }));
  }

  function viewTerms() {
    return el('section', null, [
      el('h1', { text: 'Hire terms' }),
      el('p', { 'class': 'lede', text: 'The short version of the terms every reservation is made on.' }),
      topicList('terms', [
        { key: 'status', title: 'Provisional and reserved',
          body: 'A reservation is provisional while you are putting it together. Once you finalise it, it is ' +
            'reserved, whichever way you choose to settle up.' },
        { key: 'community', title: 'Community rate',
          body: 'Registered charities and community groups receive 20% off room hire. The reduction applies to ' +
            'room hire only; catering and extras are charged at the standard price.' },
        { key: 'release', title: 'Releasing a room',
          body: 'You can release a reservation from My reservations at any time up to seven days before the ' +
            'session and owe nothing. Inside seven days, half the room hire is due.' },
        { key: 'noise', title: 'Noise and finishing times',
          body: 'Amplified sound stops at 21:30 and the building closes at 22:00. Rooms must be left as found.' },
        { key: 'catering', title: 'Catering numbers',
          body: 'Catering is prepared for the number of guests on the reservation and charged per guest.' }
      ]),
      actions([button('terms-rooms', 'Browse rooms', function () { go('rooms'); })])
    ]);
  }

  var TRAVEL = [
    { value: 'train', label: 'By train', text: 'Millrace Halt is a six-minute walk: leave by the footbridge and follow the river path upstream.' },
    { value: 'bus', label: 'By bus', text: 'Routes 12 and 40 stop at Sluice Lane, directly outside the gatehouse.' },
    { value: 'car', label: 'By car', text: 'Use the Weir Street entrance. The yard has 18 spaces, two of them wide bays by the Weaving Shed.' },
    { value: 'cycle', label: 'By bicycle', text: 'Covered stands for 30 bicycles are inside the gatehouse arch.' }
  ];

  function viewLocation() {
    var mode = optionFor(TRAVEL, state.ui.travel || 'train');
    return el('section', null, [
      el('h1', { text: 'Finding us' }),
      el('p', { 'class': 'lede', text: 'Millrace Works, 4 Sluice Lane. The hire desk is in the gatehouse.' }),
      formShell([el('div', { 'class': 'fields' }, [
        selectField({ id: 'travel-mode', name: 'travel_mode', label: 'Travelling', options: TRAVEL, value: mode.value,
          onChange: function (e) { state.ui.travel = e.target.value; refresh(); } })
      ])]),
      el('p', { 'class': 'panel', text: mode.text }),
      actions([
        button('toggle-parking', state.ui.parking ? 'Hide evening parking' : 'Show evening parking', function () {
          state.ui.parking = !state.ui.parking;
          refresh();
        })
      ]),
      state.ui.parking ? el('p', { text: 'After 18:00 the Weir Street car park opposite is free; the yard gate closes at 22:15.' }) : null
    ]);
  }

  function viewAccess() {
    var stepFreeOnly = !!state.ui.stepFreeOnly;
    var rows = ROOMS.filter(function (r) { return !stepFreeOnly || r.stepFree; }).map(function (r) {
      return el('tr', null, [
        el('th', { scope: 'row', text: r.name }),
        el('td', { text: r.stepFree ? 'Step-free' : 'Steps' }),
        el('td', { text: r.where }),
        el('td', { text: r.kit })
      ]);
    });
    var box = el('input', { type: 'checkbox', id: 'stepfree-only', name: 'stepfree_only', checked: stepFreeOnly });
    box.addEventListener('change', function () { state.ui.stepFreeOnly = box.checked; refresh(); });
    return el('section', null, [
      el('h1', { text: 'Access and facilities' }),
      el('p', { 'class': 'lede', text: 'An accessible toilet and baby change are on the ground floor by the gatehouse. ' +
        'Assistance dogs are welcome throughout.' }),
      formShell([el('ul', { 'class': 'ticks' }, [
        el('li', null, [box, el('label', { 'for': 'stepfree-only', text: 'Show step-free rooms only' })])
      ])]),
      el('section', { 'class': 'panel' }, [
        el('table', { 'class': 'charges' }, [
          el('thead', null, [el('tr', null, [
            el('th', { text: 'Room' }), el('th', { text: 'Access' }), el('th', { text: 'Where' }), el('th', { text: 'Facilities' })
          ])]),
          el('tbody', null, rows)
        ])
      ])
    ]);
  }

  var ENQUIRY_TOPICS = [
    { value: 'general', label: 'A general question' },
    { value: 'visit', label: 'Arranging a viewing' },
    { value: 'catering', label: 'Catering and dietary needs' },
    { value: 'invoice', label: 'Invoices and receipts' }
  ];

  function viewHelp() {
    var ui = state.ui;
    ui.enquiry = ui.enquiry || { topic: 'general', email: '', text: '' };
    var q = ui.enquiry;
    return el('section', null, [
      el('h1', { text: 'Help' }),
      el('p', { 'class': 'lede', text: 'Answers to the questions the desk is asked most.' }),
      topicList('faq', [
        { key: 'view', title: 'Can I see a room before I reserve it?',
          body: 'Yes. Viewings run on weekday afternoons; send the desk a note below and we will offer a time.' },
        { key: 'change', title: 'How do I change a reservation?',
          body: 'Before you finalise, use the Amend buttons on the review. Afterwards, release it from My reservations and reserve again.' },
        { key: 'size', title: 'Which room suits my group?',
          body: 'Each room lists how many it seats. For more than 45 guests choose the Weaving Shed or the Long Gallery.' },
        { key: 'deliveries', title: 'Can suppliers deliver to the room?',
          body: 'Deliveries come through the Weir Street gate from 08:30 on the day of your session.' }
      ]),
      el('h2', { text: 'Send the desk a note' }),
      ui.sent ? noticeBox('Thank you. The desk replies within one working day.') : null,
      problemsBox(ui.problems),
      formShell([el('div', { 'class': 'fields' }, [
        selectField({ id: 'enquiry-topic', name: 'enquiry_topic', label: 'About', options: ENQUIRY_TOPICS,
          value: q.topic, target: q, key: 'topic' }),
        inputField({ type: 'email', id: 'enquiry-email', name: 'enquiry_email', label: 'Your email',
          value: q.email, target: q, key: 'email' }),
        textareaField({ id: 'enquiry-text', name: 'enquiry_text', label: 'Your question', value: q.text,
          target: q, key: 'text' })
      ])]),
      actions([button('send-enquiry', 'Send to the desk', function () {
        var problems = [];
        if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(q.email.trim())) problems.push('Give an email address for the reply.');
        if (!q.text.trim()) problems.push('Write your question.');
        if (q.text.length > 2000) problems.push('Keep your question to 2000 characters or fewer.');
        if (problems.length) {
          ui.problems = problems;
          ui.sent = false;
        } else {
          ui.problems = null;
          ui.sent = true;
          ui.enquiry = { topic: 'general', email: '', text: '' };
        }
        refresh();
      }, 'main')])
    ]);
  }

  // ---------------------------------------------------------------------------------
  // rendering
  // ---------------------------------------------------------------------------------

  var VIEWS = {
    rooms: viewRooms,
    flow: viewFlow,
    reserved: viewReserved,
    reservations: viewReservations,
    terms: viewTerms,
    location: viewLocation,
    access: viewAccess,
    help: viewHelp
  };

  /** Repair a state that cannot be rendered (for example a stale view after storage loss). */
  function normalise() {
    if (!VIEWS[state.view]) state.view = 'rooms';
    if (state.view === 'flow' && !state.draft) state.view = 'rooms';
    if (state.view === 'reserved' && !findReservation(state.lastRef)) state.view = 'rooms';
    if (!state.ui) state.ui = {};
  }

  function markNav() {
    var buttons = document.querySelectorAll('.desk-nav button');
    for (var i = 0; i < buttons.length; i++) {
      if (buttons[i].getAttribute('data-view') === state.view) buttons[i].setAttribute('aria-current', 'page');
      else buttons[i].removeAttribute('aria-current');
    }
  }

  function render() {
    normalise();
    var host = document.getElementById('view');
    while (host.firstChild) host.removeChild(host.firstChild);
    host.appendChild(VIEWS[state.view]());
    markNav();
  }

  function wireNav() {
    var buttons = document.querySelectorAll('.desk-nav button');
    Array.prototype.forEach.call(buttons, function (b) {
      b.addEventListener('click', function () { go(b.getAttribute('data-view')); });
    });
  }

  wireNav();
  render();
}());
