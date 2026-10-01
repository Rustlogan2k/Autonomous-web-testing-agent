/* Tidepool account hub: shared store, pricing, and DOM helpers.
   Settings are seeded once per browser session and then kept in sessionStorage, so every
   page reads the same account. Everything here runs synchronously inside the event that
   caused it; there are no timers and no network calls. */
var Hub = (function () {
  "use strict";

  var STORAGE_KEY = "tidepool.account.v1";

  var SEED = {
    workspace: "Brightwater Studio",
    tipDismissed: false,
    profile: {
      displayName: "Imani Okafor",
      email: "imani.okafor@brightwater.example",
      language: "en-GB",
      timeZone: "Europe/London",
      timeFormat: "24",
      listed: true,
      showEmail: false,
      showRole: true
    },
    notifications: {
      emailEnabled: true, emailMentions: true, emailShares: true, emailComments: false,
      emailBilling: true, digest: "daily",
      pushEnabled: false, pushMentions: true, pushUploads: false,
      quietEnabled: false, quietFrom: "22", quietUntil: "7",
      newsFeatures: true, newsTips: false, newsResearch: false,
      pausedFor: ""
    },
    plan: { tier: "team", seats: 6, period: 1,
            addons: { support: false, history: false, audit: false } },
    planDraft: null,
    lastPlanChange: null,
    billing: { invoiceCopy: false, financeEmail: "", billingName: "Brightwater Studio Ltd",
               country: "GB" },
    team: {
      members: [
        { id: "tomasz", name: "Tomasz Wieczorek", email: "tomasz@brightwater.example", role: "admin" },
        { id: "leila", name: "Leila Haddad", email: "leila@brightwater.example", role: "editor" },
        { id: "bram", name: "Bram de Vries", email: "bram@brightwater.example", role: "editor" },
        { id: "soojin", name: "Soo-jin Park", email: "soojin@brightwater.example", role: "viewer" }
      ],
      invites: []
    },
    security: {
      passwordChanged: false, twoStep: true, method: "app", phone: "",
      sessions: [
        { id: "phone", label: "Safari on iPhone", place: "Leeds, UK" },
        { id: "laptop", label: "Firefox on Linux", place: "Leeds, UK" },
        { id: "desktop", label: "Tidepool desktop app on macOS", place: "Rotterdam, NL" }
      ]
    },
    privacy: { shareUsage: true, personalTips: true, retention: "30",
               exportRequest: null, closure: null }
  };

  var PLANS = {
    basic: { label: "Basic", rate: 600 },
    team: { label: "Team", rate: 1000 },
    scale: { label: "Scale", rate: 1800 }
  };
  var PERIODS = { "1": "Monthly", "3": "Quarterly", "12": "Yearly" };
  var SUPPORT_SEAT_RATE = 200;   /* cents, per seat, per month */
  var HISTORY_RATE = 3000;       /* cents, per month */
  var AUDIT_RATE = 4500;         /* cents, per month */

  var state = null;

  function load() {
    var raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (raw) {
      state = JSON.parse(raw);
    } else {
      state = JSON.parse(JSON.stringify(SEED));
      save();
    }
    return state;
  }

  function save() {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }

  function peopleInUse() {
    /* The owner, every member, and every invite still pending each hold a seat. */
    return 1 + state.team.members.length + state.team.invites.length;
  }

  /* What one billing period costs for a plan configuration. All amounts are whole cents.
     config = { tier, seats (integer), period (months, integer), addons: {support, history, audit} } */
  function quote(config) {
    var months = config.period;
    var seatCharge = config.seats * PLANS[config.tier].rate * months;
    var saving = months === 12 ? Math.round(seatCharge / 10) : 0;
    var supportCharge = config.seats * SUPPORT_SEAT_RATE;
    var historyCharge = HISTORY_RATE * months;
    var auditCharge = AUDIT_RATE * months;
    var result = {
      seatCharge: seatCharge,
      saving: saving,
      support: config.addons.support ? supportCharge : null,
      history: config.addons.history ? historyCharge : null,
      audit: config.addons.audit ? auditCharge : null
    };
    result.total = seatCharge - saving + (result.support || 0) + (result.history || 0) +
      (result.audit || 0);
    return result;
  }

  function money(cents) {
    /* No thousands separator on purpose: amounts are read back as plain numbers. */
    return "$" + (cents / 100).toFixed(2);
  }

  function isEmail(text) {
    return text.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(text);
  }

  /* ---- DOM helpers ------------------------------------------------------------------ */

  function h(tag, attrs, kids) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      var value = attrs[key];
      if (value === null || value === undefined || value === false) { return; }
      if (key === "text") {
        node.textContent = value;
      } else if (key.slice(0, 2) === "on") {
        node.addEventListener(key.slice(2), value);
      } else if (key === "checked") {
        node.checked = true;
      } else if (key === "value" && tag !== "option") {
        node.value = value;
      } else {
        node.setAttribute(key, value === true ? "" : value);
      }
    });
    (kids || []).forEach(function (kid) {
      if (kid === null || kid === undefined || kid === false) { return; }
      node.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid);
    });
    return node;
  }

  function fill(container, kids) {
    while (container.firstChild) { container.removeChild(container.firstChild); }
    (kids || []).forEach(function (kid) {
      if (kid === null || kid === undefined || kid === false) { return; }
      container.appendChild(kid);
    });
  }

  /* A labelled text-like input. `oninput` is wired to both events a typed value can raise. */
  function field(options) {
    var input = h("input", {
      type: options.type || "text", id: options.id, name: options.name,
      autocomplete: "off", value: options.value || "",
      oninput: options.oninput, onchange: options.oninput
    });
    return h("div", { "class": "field" }, [
      h("label", { "for": options.id, text: options.label }),
      input,
      options.hint ? h("span", { "class": "hint", text: options.hint }) : null
    ]);
  }

  function picker(options) {
    var select = h("select", { id: options.id, name: options.name, autocomplete: "off",
                               onchange: options.onchange });
    options.options.forEach(function (pair) {
      select.appendChild(h("option", { value: pair[0], text: pair[1] }));
    });
    select.value = options.value;
    return h("div", { "class": "field" }, [
      h("label", { "for": options.id, text: options.label }),
      select,
      options.hint ? h("span", { "class": "hint", text: options.hint }) : null
    ]);
  }

  /* A checkbox or radio with its label. `kind` is "switch", "checkbox" or "radio". */
  function choice(options) {
    var isRadio = options.kind === "radio";
    return h("div", { "class": "choice" }, [
      h("input", {
        type: isRadio ? "radio" : "checkbox", id: options.id, name: options.name,
        "class": options.kind === "switch" ? "switch" : null,
        value: options.value || "on", checked: !!options.checked, autocomplete: "off",
        onchange: options.onchange
      }),
      h("label", { "for": options.id, text: options.label })
    ]);
  }

  function button(id, label, onclick, style) {
    return h("button", { type: "button", id: id, "class": style || null, text: label,
                         onclick: onclick });
  }

  function note(text, tone, id) {
    if (!text) { return null; }
    return h("p", { "class": "note " + (tone || "plain"), id: id || null, text: text });
  }

  function fact(term, value, id, extraClass) {
    return [
      h("dt", { text: term, "class": extraClass || null }),
      h("dd", { text: value, id: id || null, "class": extraClass || null })
    ];
  }

  function facts(rows) {
    var list = h("dl", { "class": "facts" });
    rows.forEach(function (row) {
      if (!row) { return; }
      list.appendChild(row[0]);
      list.appendChild(row[1]);
    });
    return list;
  }

  /* ---- page chrome: who is signed in, and the closure banner ---------------------------- */

  var pageRefresh = null;

  function renderChrome() {
    var identity = document.getElementById("hub-identity");
    if (identity) {
      fill(identity, [
        h("strong", { text: state.profile.displayName }),
        h("span", { text: state.profile.email }),
        h("span", { text: state.workspace + " · Owner" })
      ]);
    }
    var banner = document.getElementById("hub-banner");
    if (banner) {
      if (state.privacy.closure) {
        fill(banner, [h("div", { "class": "banner" }, [
          h("p", { text: "This workspace is set to close. Files stay available to members for " +
                         "30 days, then they are erased." }),
          button("reopen-workspace", "Reopen workspace", function () {
            state.privacy.closure = null;
            save();
            renderChrome();
            if (pageRefresh) { pageRefresh(); }
          }, "ghost")
        ])]);
      } else {
        fill(banner, []);
      }
    }
  }

  function start(refresh) {
    pageRefresh = refresh || null;
    renderChrome();
  }

  load();

  return {
    state: function () { return state; },
    save: save,
    start: start,
    renderChrome: renderChrome,
    PLANS: PLANS,
    PERIODS: PERIODS,
    quote: quote,
    money: money,
    isEmail: isEmail,
    peopleInUse: peopleInUse,
    h: h, fill: fill, field: field, picker: picker, choice: choice, button: button,
    note: note, fact: fact, facts: facts
  };
})();
