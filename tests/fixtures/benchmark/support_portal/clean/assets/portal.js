/* Larkfield Broadband care desk.

   One script for every page. A request is a draft held in sessionStorage and filled in
   over several pages; which pages it passes through depends on the answers given so far
   (stagesFor). Every stage page asks the same two questions before it shows its form:
   is this page on the route for the draft as stored, and do all the earlier stages on
   that route still pass their own checks? The checks used by a stage's own button and
   by the gates of the later pages are the same functions, so a page can never be open
   for a draft its predecessor would have refused. */
(function () {
  'use strict';

  var DRAFT_KEY = 'larkfield.draft';
  var SENT_KEY = 'larkfield.sent';

  // ---------------------------------------------------------------- reference data

  var TOPICS = {
    fault: { label: 'Connection is down or slow', code: 'FLT' },
    billing: { label: 'A bill or payment', code: 'BIL' },
    moving: { label: 'Moving home', code: 'MOV' },
    other: { label: 'Something else', code: 'GEN' }
  };
  var URGENCY = {
    low: 'Minor inconvenience',
    normal: 'Noticeable, but I can work around it',
    high: 'Stopping me working or studying',
    outage: 'No service at all'
  };
  var SINCE = { today: 'Today', week: 'In the last week', longer: 'More than a week ago' };
  var LIGHTS = {
    green: 'All lights green',
    red: 'Internet light is red',
    amber: 'Lights flashing amber',
    dark: 'No lights at all'
  };
  var DEVICES = { all: 'Every device', some: 'Some devices', one: 'One device only' };
  var SLOTS = {
    am: 'Morning, 8am to 12pm',
    pm: 'Afternoon, 12pm to 5pm',
    eve: 'Evening, 5pm to 8pm'
  };
  var CHARGES = {
    monthly: 'Monthly plan charge',
    callout: 'Engineer call-out fee',
    late: 'Late payment fee',
    exit: 'Early exit fee',
    kit: 'Equipment charge'
  };
  var METHODS = { email: 'Email', phone: 'Phone call', text: 'Text message' };
  var TIMES = {
    anytime: 'Any time',
    morning: 'Mornings',
    afternoon: 'Afternoons',
    evening: 'Evenings'
  };

  var STAGE_PAGE = {
    topic: 'request-topic.html',
    details: 'request-details.html',
    line: 'request-line.html',
    visit: 'request-visit.html',
    bill: 'request-bill.html',
    move: 'request-move.html',
    contact: 'request-contact.html',
    consent: 'request-consent.html',
    review: 'request-review.html'
  };
  var STAGE_TITLE = {
    topic: 'Topic',
    details: 'What is happening',
    line: 'Line checks',
    visit: 'Engineer visit',
    bill: 'The charge',
    move: 'Your move',
    contact: 'Contact details',
    consent: 'Extras and consent',
    review: 'Check your answers'
  };
  var ADVANCE_BUTTON = {
    topic: 'use-topic',
    details: 'save-details',
    line: 'save-line',
    visit: 'save-visit',
    bill: 'save-bill',
    move: 'save-move',
    contact: 'save-contact',
    consent: 'check-answers'
  };

  // Which controls belong to which stage. `value` covers text inputs, textareas and
  // selects; `radio` is a group sharing a name; `check` is a single checkbox.
  var FIELDS = {
    topic: [{ name: 'topic', kind: 'radio' }],
    details: [
      { name: 'summary', kind: 'value' },
      { name: 'description', kind: 'value' },
      { name: 'urgency', kind: 'value' },
      { name: 'since', kind: 'value' }
    ],
    line: [
      { name: 'router_lights', kind: 'value' },
      { name: 'devices_affected', kind: 'value' },
      { name: 'restarted', kind: 'check' },
      { name: 'wired_test', kind: 'check' }
    ],
    visit: [
      { name: 'visit_slot', kind: 'radio' },
      { name: 'access_notes', kind: 'value' },
      { name: 'adult_present', kind: 'check' }
    ],
    bill: [
      { name: 'invoice_ref', kind: 'value' },
      { name: 'disputed_amount', kind: 'value' },
      { name: 'charge_kind', kind: 'value' },
      { name: 'paid_already', kind: 'check' }
    ],
    move: [
      { name: 'new_postcode', kind: 'value' },
      { name: 'move_date', kind: 'value' },
      { name: 'keep_number', kind: 'check' }
    ],
    contact: [
      { name: 'full_name', kind: 'value' },
      { name: 'email', kind: 'value' },
      { name: 'phone', kind: 'value' },
      { name: 'contact_method', kind: 'radio' },
      { name: 'best_time', kind: 'value' }
    ],
    consent: [
      { name: 'confirm_accurate', kind: 'check' },
      { name: 'agree_contact', kind: 'check' },
      { name: 'remote_test', kind: 'check' },
      { name: 'sms_updates', kind: 'check' }
    ]
  };

  // ---------------------------------------------------------------- storage

  function readJson(key, fallback) {
    try {
      var raw = sessionStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (e) {
      return fallback;
    }
  }
  function writeJson(key, value) {
    try { sessionStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* storage off */ }
  }
  function loadDraft() { return readJson(DRAFT_KEY, {}); }
  function saveDraft(draft) { writeJson(DRAFT_KEY, draft); }
  function dropDraft() {
    try { sessionStorage.removeItem(DRAFT_KEY); } catch (e) { /* storage off */ }
  }
  function loadSent() { return readJson(SENT_KEY, []); }
  function saveSent(list) { writeJson(SENT_KEY, list); }

  // ---------------------------------------------------------------- small helpers

  function byId(id) { return document.getElementById(id); }
  function show(node) { if (node) { node.hidden = false; } }
  function hide(node) { if (node) { node.hidden = true; } }
  function str(d, name) { return typeof d[name] === 'string' ? d[name].trim() : ''; }
  function clip(value, limit) {
    var text = String(value === undefined ? '' : value).trim();
    return text.length > limit ? text.slice(0, limit) + '...' : text;
  }
  function yesNo(flag) { return flag === true ? 'Yes' : 'No'; }
  function money(value) { return '£' + Number(value).toFixed(2); }
  function phoneDigits(value) { return String(value || '').replace(/[\s()+-]/g, ''); }
  function make(tag, text, id) {
    var node = document.createElement(tag);
    if (text !== undefined) { node.textContent = text; }
    if (id) { node.id = id; }
    return node;
  }
  function empty(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }

  // ---------------------------------------------------------------- the route

  // The stages a draft passes through, in order. The branch stage depends on the topic;
  // an engineer visit is offered only for a connection fault reported as a total loss
  // of service. "Something else" has no branch stage at all.
  function stagesFor(d) {
    var list = ['topic', 'details'];
    if (d.topic === 'fault') { list.push('line'); }
    if (d.topic === 'fault' && d.urgency === 'outage') { list.push('visit'); }
    if (d.topic === 'billing') { list.push('bill'); }
    if (d.topic === 'moving') { list.push('move'); }
    list.push('contact', 'consent', 'review');
    return list;
  }

  // ---------------------------------------------------------------- the rules

  var EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
  var CHECKS = {};

  CHECKS.topic = function (d) {
    return TOPICS[d.topic] ? [] : ['Choose what you need help with.'];
  };

  CHECKS.details = function (d) {
    var problems = [];
    var summary = str(d, 'summary');
    var description = str(d, 'description');
    if (summary.length < 5 || summary.length > 80) {
      problems.push('Give the request a short title of 5 to 80 characters.');
    }
    if (description.length < 20) {
      problems.push('Describe what is happening in at least 20 characters.');
    } else if (description.length > 2000) {
      problems.push('Keep the description under 2,000 characters.');
    }
    if (!URGENCY[d.urgency]) {
      problems.push('Tell us how much this is affecting you.');
    }
    return problems;
  };

  CHECKS.line = function (d) {
    var problems = [];
    if (!LIGHTS[d.router_lights]) {
      problems.push('Tell us what the lights on the router are doing.');
    }
    if (d.restarted !== true) {
      problems.push('Restart the router and tick the box before we log a fault.');
    }
    return problems;
  };

  CHECKS.visit = function (d) {
    var problems = [];
    if (!SLOTS[d.visit_slot]) {
      problems.push('Pick a time of day for the engineer visit.');
    }
    if (str(d, 'access_notes').length > 120) {
      problems.push('Keep the access notes under 120 characters.');
    }
    if (d.adult_present !== true) {
      problems.push('Someone aged 18 or over must be at home for the visit.');
    }
    return problems;
  };

  CHECKS.bill = function (d) {
    var problems = [];
    var given = str(d, 'disputed_amount');
    var amount = Number(given);
    if (str(d, 'invoice_ref').length > 20) {
      problems.push('An invoice number is at most 20 characters.');
    }
    if (given === '' || isNaN(amount)) {
      problems.push('Enter the amount you are querying.');
    } else if (amount <= 0) {
      problems.push('The amount you are querying must be more than £0.');
    } else if (amount > 5000) {
      problems.push('For amounts over £5,000 please call the accounts team.');
    }
    if (!CHARGES[d.charge_kind]) {
      problems.push('Choose the kind of charge.');
    }
    return problems;
  };

  CHECKS.move = function (d) {
    var problems = [];
    var postcode = str(d, 'new_postcode');
    var parts = /^(\d{4})-\d{2}-\d{2}$/.exec(str(d, 'move_date'));
    if (postcode.length < 3 || postcode.length > 20) {
      problems.push('Enter the postcode of the new address (3 to 20 characters).');
    }
    if (!parts) {
      problems.push('Enter the date you are moving.');
    } else if (Number(parts[1]) < 2024 || Number(parts[1]) > 2030) {
      problems.push('We can log moves dated from 2024 to 2030.');
    }
    return problems;
  };

  CHECKS.contact = function (d) {
    var problems = [];
    var name = str(d, 'full_name');
    var email = str(d, 'email');
    var phone = str(d, 'phone');
    var digits = phoneDigits(phone);
    var phoneNeeded = d.contact_method === 'phone' || d.contact_method === 'text';
    if (name.length < 2 || name.length > 60) {
      problems.push('Enter your name (2 to 60 characters).');
    }
    if (!EMAIL.test(email) || email.length > 254) {
      problems.push('Enter a valid email address. We send your reference to it.');
    }
    if (!METHODS[d.contact_method]) {
      problems.push('Choose how you would like us to reach you.');
    }
    if (phone === '' && phoneNeeded) {
      problems.push('Enter a phone number, or choose email as the way to reach you.');
    } else if (phone !== '' && !/^\d{7,15}$/.test(digits)) {
      problems.push('A phone number has 7 to 15 digits.');
    }
    return problems;
  };

  CHECKS.consent = function (d) {
    var problems = [];
    if (d.confirm_accurate !== true) {
      problems.push('Confirm that the details you have given are accurate.');
    }
    if (d.agree_contact !== true) {
      problems.push('We need your agreement to get in touch about this request.');
    }
    if (d.sms_updates === true && str(d, 'phone') === '') {
      problems.push('Text updates need a phone number. Add one under contact details.');
    }
    return problems;
  };

  // ---------------------------------------------------------------- gates

  // The first stage on the draft's route that does not pass, or `review` when all do.
  function firstUnfinished(d) {
    var route = stagesFor(d);
    for (var i = 0; i < route.length; i++) {
      if (route[i] === 'review' || CHECKS[route[i]](d).length) { return route[i]; }
    }
    return 'review';
  }

  // Whether `stage` may be shown for draft `d`: it must be on the route and every stage
  // before it must pass.
  function gateFor(stage, d) {
    var route = stagesFor(d);
    var position = route.indexOf(stage);
    var pending = firstUnfinished(d);
    if (position < 0) { return { open: false, offRoute: true, pending: pending }; }
    if (route.indexOf(pending) < position) { return { open: false, offRoute: false, pending: pending }; }
    return { open: true, route: route, position: position };
  }

  // ---------------------------------------------------------------- answers

  // How each stored answer is shown back. `brief` rows also appear in the "answers so
  // far" panel beside the later stages; the summary page shows every row.
  var ANSWERS = {
    topic: [
      { key: 'topic', label: 'Topic', brief: true, show: function (d) { return TOPICS[d.topic].label; } }
    ],
    details: [
      { key: 'summary', label: 'Short title', brief: true, show: function (d) { return clip(d.summary, 80); } },
      { key: 'description', label: 'What is happening', show: function (d) { return clip(d.description, 240); } },
      { key: 'urgency', label: 'Impact', brief: true, show: function (d) { return URGENCY[d.urgency]; } },
      { key: 'since', label: 'Started', show: function (d) { return SINCE[d.since] || 'Not said'; } }
    ],
    line: [
      { key: 'lights', label: 'Router lights', brief: true, show: function (d) { return LIGHTS[d.router_lights]; } },
      { key: 'devices', label: 'Devices affected', show: function (d) { return DEVICES[d.devices_affected] || 'Not said'; } },
      { key: 'restarted', label: 'Router restarted', brief: true, show: function (d) { return yesNo(d.restarted); } },
      { key: 'wired', label: 'Tested with a cable', show: function (d) { return yesNo(d.wired_test); } }
    ],
    visit: [
      { key: 'slot', label: 'Engineer visit', brief: true, show: function (d) { return SLOTS[d.visit_slot]; } },
      { key: 'access', label: 'Access notes', show: function (d) { return clip(d.access_notes, 120) || 'None'; } },
      { key: 'adult', label: 'Adult at home', show: function (d) { return yesNo(d.adult_present); } }
    ],
    bill: [
      { key: 'invoice', label: 'Invoice number', brief: true, show: function (d) { return clip(d.invoice_ref, 20) || 'Not given'; } },
      { key: 'amount', label: 'Amount queried', brief: true, show: function (d) { return money(d.disputed_amount); } },
      { key: 'charge', label: 'Kind of charge', show: function (d) { return CHARGES[d.charge_kind]; } },
      { key: 'paid', label: 'Already paid', show: function (d) { return yesNo(d.paid_already); } }
    ],
    move: [
      { key: 'postcode', label: 'New postcode', brief: true, show: function (d) { return clip(d.new_postcode, 20); } },
      { key: 'movedate', label: 'Moving on', brief: true, show: function (d) { return str(d, 'move_date'); } },
      { key: 'keepnumber', label: 'Keep landline number', show: function (d) { return yesNo(d.keep_number); } }
    ],
    contact: [
      { key: 'name', label: 'Name', brief: true, show: function (d) { return clip(d.full_name, 60); } },
      { key: 'email', label: 'Email', brief: true, show: function (d) { return str(d, 'email'); } },
      { key: 'phone', label: 'Phone', show: function (d) { return str(d, 'phone') || 'Not given'; } },
      { key: 'method', label: 'Reach me by', brief: true, show: function (d) { return METHODS[d.contact_method]; } },
      { key: 'besttime', label: 'Best time', show: function (d) { return TIMES[d.best_time] || 'Any time'; } }
    ],
    consent: [
      { key: 'accurate', label: 'Details confirmed accurate', show: function (d) { return yesNo(d.confirm_accurate); } },
      { key: 'agree', label: 'Agreed to be contacted', show: function (d) { return yesNo(d.agree_contact); } },
      { key: 'remote', label: 'Remote line test allowed', show: function (d) { return yesNo(d.remote_test); } },
      { key: 'sms', label: 'Text updates', show: function (d) { return yesNo(d.sms_updates); } }
    ]
  };

  function appendAnswer(list, prefix, row, d) {
    list.appendChild(make('dt', row.label));
    list.appendChild(make('dd', row.show(d), prefix + '-' + row.key));
  }

  // ---------------------------------------------------------------- form plumbing

  function controls(name) { return document.querySelectorAll('[name="' + name + '"]'); }

  function readField(field) {
    var nodes = controls(field.name);
    var i;
    if (!nodes.length) { return undefined; }
    if (field.kind === 'radio') {
      for (i = 0; i < nodes.length; i++) { if (nodes[i].checked) { return nodes[i].value; } }
      return '';
    }
    if (field.kind === 'check') { return nodes[0].checked === true; }
    return nodes[0].value;
  }

  function writeField(field, value) {
    var nodes = controls(field.name);
    var i;
    if (!nodes.length || value === undefined) { return; }
    if (field.kind === 'radio') {
      for (i = 0; i < nodes.length; i++) { nodes[i].checked = nodes[i].value === value; }
    } else if (field.kind === 'check') {
      nodes[0].checked = value === true;
    } else {
      nodes[0].value = value;
    }
  }

  function readStage(stage, into) {
    FIELDS[stage].forEach(function (field) {
      var value = readField(field);
      if (value !== undefined) { into[field.name] = value; }
    });
    return into;
  }

  function showProblems(problems) {
    var box = byId('stage-warning');
    var list = byId('warning-list');
    empty(list);
    problems.forEach(function (problem) { list.appendChild(make('li', problem)); });
    show(box);
  }

  function say(message) {
    var note = byId('stage-note');
    note.textContent = message;
    show(note);
  }

  // ---------------------------------------------------------------- a stage page

  function showLocked(gate) {
    var reason = byId('locked-reason');
    var go = byId('locked-go');
    reason.textContent = gate.offRoute
      ? 'This step is not part of the request you are making.'
      : 'This step opens once "' + STAGE_TITLE[gate.pending] + '" is complete.';
    go.textContent = 'Go to "' + STAGE_TITLE[gate.pending] + '"';
    go.setAttribute('href', STAGE_PAGE[gate.pending]);
    show(byId('stage-locked'));
  }

  function renderSteps(route, position) {
    var list = byId('step-list');
    if (!list) { return; }
    empty(list);
    route.forEach(function (name, index) {
      var item = make('li', STAGE_TITLE[name]);
      if (index < position) { item.className = 'done'; }
      if (index === position) { item.className = 'current'; }
      list.appendChild(item);
    });
    show(list);
  }

  function renderSoFar(route, position, d) {
    var panel = byId('sofar');
    var list = byId('sofar-list');
    if (!panel || !list || position === 0) { return; }
    empty(list);
    route.slice(0, position).forEach(function (name) {
      ANSWERS[name].forEach(function (row) {
        if (row.brief) { appendAnswer(list, 'sofar', row, d); }
      });
    });
    show(panel);
  }

  function initStage(stage) {
    var draft = loadDraft();
    var gate = gateFor(stage, draft);
    var amending = /[?&]amend=1(&|$)/.test(window.location.search);
    var form = byId('stage-form');
    var back = byId('back-step');

    if (!gate.open) { showLocked(gate); return; }

    FIELDS[stage].forEach(function (field) { writeField(field, draft[field.name]); });
    renderSteps(gate.route, gate.position);
    renderSoFar(gate.route, gate.position, draft);
    if (back && gate.position > 0) {
      back.setAttribute('href', STAGE_PAGE[gate.route[gate.position - 1]]);
    }
    if (stage === 'consent') {
      // A remote line test only means something for a connection fault.
      if (draft.topic !== 'fault') { hide(byId('remote-test-row')); }
    }
    if (amending) { show(byId('amend-note')); }
    show(form);

    form.addEventListener('submit', function (event) { event.preventDefault(); });

    byId(ADVANCE_BUTTON[stage]).addEventListener('click', function () {
      var candidate = readStage(stage, loadDraft());
      var problems = CHECKS[stage](candidate);
      var route;
      var next;
      hide(byId('stage-note'));
      if (problems.length) { showProblems(problems); return; }
      hide(byId('stage-warning'));
      saveDraft(candidate);
      route = stagesFor(candidate);
      next = route[route.indexOf(stage) + 1];
      // Coming from the summary to change one answer: go straight back there, unless
      // the change opened a step that still needs filling in.
      if (amending && gateFor('review', candidate).open) { next = 'review'; }
      window.location.href = STAGE_PAGE[next];
    });

    byId('save-draft').addEventListener('click', function () {
      saveDraft(readStage(stage, loadDraft()));
      hide(byId('stage-warning'));
      say('Draft saved. It is kept until you close this tab.');
    });

    byId('clear-step').addEventListener('click', function () {
      var current = loadDraft();
      FIELDS[stage].forEach(function (field) { delete current[field.name]; });
      saveDraft(current);
      form.reset();
      hide(byId('stage-warning'));
      say('This step has been cleared.');
    });

    wireDiscard();
  }

  function wireDiscard() {
    var panel = byId('discard-panel');
    byId('discard-request').addEventListener('click', function () { show(panel); });
    byId('discard-no').addEventListener('click', function () { hide(panel); });
    byId('discard-yes').addEventListener('click', function () {
      dropDraft();
      window.location.href = 'index.html';
    });
  }

  // ---------------------------------------------------------------- summary page

  function initReview() {
    var draft = loadDraft();
    var gate = gateFor('review', draft);
    var holder = byId('review-sections');
    var sending = false;

    if (!gate.open) { showLocked(gate); return; }

    renderSteps(gate.route, gate.position);
    gate.route.slice(0, gate.position).forEach(function (name) {
      var section = make('section');
      var list = make('dl');
      var change = make('a', 'Change ' + STAGE_TITLE[name].toLowerCase(), 'amend-' + name);
      section.className = 'review-block';
      section.appendChild(make('h2', STAGE_TITLE[name]));
      list.className = 'answers';
      ANSWERS[name].forEach(function (row) { appendAnswer(list, 'review', row, draft); });
      section.appendChild(list);
      change.setAttribute('href', STAGE_PAGE[name] + '?amend=1');
      change.className = 'plain-link';
      section.appendChild(change);
      holder.appendChild(section);
    });
    byId('back-step').setAttribute('href', STAGE_PAGE[gate.route[gate.position - 1]]);
    show(byId('review-panel'));

    byId('send-request').addEventListener('click', function () {
      var current = loadDraft();
      var sent = loadSent();
      // A second click before the next page has loaded must not send a second request.
      if (sending || !gateFor('review', current).open) { return; }
      sending = true;
      sent.push({
        reference: 'LB-' + TOPICS[current.topic].code + '-' + (4100 + sent.length + 1),
        topic: TOPICS[current.topic].label,
        title: clip(current.summary, 80),
        impact: URGENCY[current.urgency],
        reachBy: METHODS[current.contact_method],
        email: str(current, 'email'),
        status: 'Received'
      });
      saveSent(sent);
      dropDraft();
      window.location.href = 'request-sent.html';
    });

    wireDiscard();
  }

  // ---------------------------------------------------------------- sent page

  function initSent() {
    var sent = loadSent();
    var latest = sent[sent.length - 1];
    var list = byId('sent-list');
    if (!latest) { show(byId('sent-none')); return; }
    byId('sent-reference').textContent = latest.reference;
    [['Topic', latest.topic, 'sent-topic'], ['Short title', latest.title, 'sent-title'],
      ['Impact', latest.impact, 'sent-impact'], ['We will reach you by', latest.reachBy, 'sent-method'],
      ['Reference sent to', latest.email, 'sent-email']].forEach(function (row) {
      list.appendChild(make('dt', row[0]));
      list.appendChild(make('dd', row[1], row[2]));
    });
    byId('track-this').setAttribute('href', 'track.html?ref=' + encodeURIComponent(latest.reference));
    show(byId('sent-panel'));
  }

  // ---------------------------------------------------------------- track page

  var ARCHIVE = {
    '12345': {
      reference: '12345',
      topic: 'Connection is down or slow',
      title: 'Router replaced after storm damage',
      status: 'Closed',
      note: 'A replacement router was delivered and the line tested clear.'
    }
  };

  function findRequest(reference) {
    var sent = loadSent();
    for (var i = 0; i < sent.length; i++) {
      if (sent[i].reference === reference) { return { record: sent[i], index: i }; }
    }
    return ARCHIVE[reference] ? { record: ARCHIVE[reference], index: -1 } : null;
  }

  function initTrack() {
    var field = byId('reference');
    var warning = byId('track-warning');
    var result = byId('track-result');
    var facts = byId('track-facts');
    var withdraw = byId('withdraw-request');
    var mine = byId('track-mine');
    var shown = null;

    function listMine() {
      var sent = loadSent();
      empty(mine);
      sent.forEach(function (record, index) {
        var item = make('li');
        var link = make('a', record.reference, 'mine-' + (index + 1));
        link.setAttribute('href', 'track.html?ref=' + encodeURIComponent(record.reference));
        item.appendChild(link);
        item.appendChild(document.createTextNode(' - ' + record.title + ' (' + record.status + ')'));
        mine.appendChild(item);
      });
      if (sent.length) { show(byId('track-mine-panel')); }
    }

    function reveal(found) {
      var record = found.record;
      shown = found;
      empty(facts);
      [['Reference', record.reference, 'track-reference'], ['Topic', record.topic, 'track-topic'],
        ['Short title', record.title, 'track-title'], ['Progress', record.status, 'track-status'],
        ['Latest note', record.note || (record.status === 'Withdrawn'
          ? 'You withdrew this request. No further action will be taken.'
          : 'Waiting for an adviser to pick this up.'), 'track-note']].forEach(function (row) {
        facts.appendChild(make('dt', row[0]));
        facts.appendChild(make('dd', row[1], row[2]));
      });
      if (found.index >= 0 && record.status === 'Received') { show(withdraw); } else { hide(withdraw); }
      hide(warning);
      show(result);
    }

    function lookup(reference) {
      var found = findRequest(reference);
      if (reference === '') {
        warning.textContent = 'Enter the reference from your confirmation email.';
      } else if (!found) {
        warning.textContent = 'We could not find a request with that reference. Check it and try again.';
      }
      if (!found) { hide(result); show(warning); shown = null; return; }
      reveal(found);
    }

    byId('track-form').addEventListener('submit', function (event) { event.preventDefault(); });
    byId('find-request').addEventListener('click', function () { lookup(field.value.trim()); });
    withdraw.addEventListener('click', function () {
      var sent = loadSent();
      if (!shown || shown.index < 0 || !sent[shown.index]) { return; }
      sent[shown.index].status = 'Withdrawn';
      saveSent(sent);
      listMine();
      reveal({ record: sent[shown.index], index: shown.index });
    });

    listMine();
    var asked = /[?&]ref=([^&]+)/.exec(window.location.search);
    if (asked) { lookup(decodeURIComponent(asked[1])); }
  }

  // ---------------------------------------------------------------- home and help

  function initHome() {
    var draft = loadDraft();
    var sent = loadSent();
    var resume = byId('resume-draft');
    if (Object.keys(draft).length) {
      resume.setAttribute('href', STAGE_PAGE[firstUnfinished(draft)]);
      show(byId('resume-box'));
    }
    if (sent.length) {
      byId('raised-count').textContent = String(sent.length);
      show(byId('raised-box'));
    }
  }

  function initHelp() {
    var note = byId('useful-note');
    var yes = byId('useful-yes');
    var no = byId('useful-no');
    if (!note || !yes || !no) { return; }
    yes.addEventListener('click', function () {
      note.textContent = 'Thank you. We will keep this article as it is.';
      show(note);
    });
    no.addEventListener('click', function () {
      note.textContent = 'Sorry about that. Raise a request and an adviser will help.';
      show(note);
    });
  }

  // ---------------------------------------------------------------- boot

  var body = document.body;
  var stage = body.getAttribute('data-stage');
  var page = body.getAttribute('data-page');
  if (stage) {
    initStage(stage);
  } else if (page === 'review') {
    initReview();
  } else if (page === 'sent') {
    initSent();
  } else if (page === 'track') {
    initTrack();
  } else if (page === 'home') {
    initHome();
  } else if (page === 'help') {
    initHelp();
  }
})();
