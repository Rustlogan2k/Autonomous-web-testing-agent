/* Plan & billing page. The plan calculator keeps a draft in the shared store so it survives a
   reload; the draft only ever changes through its own controls. A receipt is shown after a
   change is applied and is cleared as soon as the calculator is opened again, so the receipt
   on screen always describes the configuration that was last entered. */
(function () {
  "use strict";
  var h = Hub.h, state = Hub.state();

  function addonNames(addons) {
    var names = [];
    if (addons.support) { names.push("Priority support"); }
    if (addons.history) { names.push("Extended version history"); }
    if (addons.audit) { names.push("Audit log export"); }
    return names.length ? names.join(", ") : "None";
  }

  function periodPhrase(period) {
    return { "1": "every month", "3": "every 3 months", "12": "every year" }[String(period)];
  }

  /* ---- current plan (read only) -------------------------------------------------------- */
  var currentBody = document.getElementById("current-plan-body");

  function renderCurrent() {
    var plan = state.plan, cost = Hub.quote(plan);
    Hub.fill(currentBody, [
      Hub.facts([
        Hub.fact("Plan", Hub.PLANS[plan.tier].label, "current-plan-name"),
        Hub.fact("Seats", plan.seats + " (" + Hub.peopleInUse() + " in use)", "current-plan-seats"),
        Hub.fact("Billed", Hub.PERIODS[String(plan.period)], "current-plan-period"),
        Hub.fact("Add-ons", addonNames(plan.addons), "current-plan-addons"),
        Hub.fact("Recurring charge", Hub.money(cost.total) + " " + periodPhrase(plan.period),
                 "current-plan-charge", "grand")
      ])
    ]);
  }

  /* ---- plan calculator ----------------------------------------------------------------- */
  var calcBody = document.getElementById("calculator-body");
  var stage = "closed";               /* closed | edit | review */
  var authorised = false, calcProblem = "";

  function draft() {
    if (!state.planDraft) {
      state.planDraft = {
        tier: state.plan.tier, seats: String(state.plan.seats), period: String(state.plan.period),
        addons: { support: state.plan.addons.support, history: state.plan.addons.history,
                  audit: state.plan.addons.audit }
      };
      Hub.save();
    }
    return state.planDraft;
  }

  /* The draft as a priced configuration, or the reason it cannot be priced. */
  function readDraft() {
    var d = draft();
    if (!/^\d+$/.test(d.seats)) {
      return { problem: "Enter a whole number of seats between 1 and 250." };
    }
    var seats = parseInt(d.seats, 10);
    if (seats < 1 || seats > 250) {
      return { problem: "Enter a whole number of seats between 1 and 250." };
    }
    if (seats < Hub.peopleInUse()) {
      return { problem: "There are " + Hub.peopleInUse() + " people holding seats. Remove " +
                        "someone in Team & seats before going below that." };
    }
    return { config: { tier: d.tier, seats: seats, period: parseInt(d.period, 10),
                       addons: { support: d.addons.support, history: d.addons.history,
                                 audit: d.addons.audit } } };
  }

  function sameAsCurrent(config) {
    var plan = state.plan;
    return config.tier === plan.tier && config.seats === plan.seats &&
      config.period === plan.period && config.addons.support === plan.addons.support &&
      config.addons.history === plan.addons.history && config.addons.audit === plan.addons.audit;
  }

  function chargeRows(prefix, config, cost) {
    return [
      Hub.fact(config.seats + " seats on " + Hub.PLANS[config.tier].label + ", " +
               Hub.PERIODS[String(config.period)].toLowerCase(),
               Hub.money(cost.seatCharge), prefix + "-seat-charge"),
      cost.saving ? Hub.fact("Yearly saving (10% of seat charges)", "-" + Hub.money(cost.saving),
                             prefix + "-saving") : null,
      cost.support !== null ? Hub.fact("Priority support, per seat", Hub.money(cost.support),
                                       prefix + "-support-charge") : null,
      cost.history !== null ? Hub.fact("Extended version history", Hub.money(cost.history),
                                       prefix + "-history-charge") : null,
      cost.audit !== null ? Hub.fact("Audit log export", Hub.money(cost.audit),
                                     prefix + "-audit-charge") : null,
      Hub.fact("Recurring charge, " + periodPhrase(config.period), Hub.money(cost.total),
               prefix + "-recurring-charge", "grand")
    ];
  }

  function estimateNodes() {
    var read = readDraft();
    if (read.problem) { return [Hub.note(read.problem, "plain")]; }
    return [Hub.facts(chargeRows("estimate", read.config, Hub.quote(read.config)))];
  }

  function renderCalculator() {
    if (stage === "closed") {
      var receipt = state.lastPlanChange;
      Hub.fill(calcBody, [
        h("div", { "class": "actions" }, [
          Hub.button("open-plan-editor", "Change plan or seats", function () {
            state.lastPlanChange = null;
            draft();
            Hub.save();
            stage = "edit"; calcProblem = "";
            renderCalculator();
          })
        ]),
        receipt ? h("div", { "class": "guard calm", id: "plan-receipt" }, [
          h("h3", { text: "Plan change applied" }),
          h("p", { text: "Your workspace is now on " + Hub.PLANS[receipt.config.tier].label +
                         " with " + receipt.config.seats + " seats. Add-ons: " +
                         addonNames(receipt.config.addons) + "." }),
          Hub.facts(chargeRows("receipt", receipt.config, receipt.cost))
        ]) : null
      ]);
      return;
    }

    var d = draft();

    if (stage === "edit") {
      var estimate = h("div", { id: "plan-estimate" }, estimateNodes());
      var refreshEstimate = function () { Hub.fill(estimate, estimateNodes()); };
      var pickTier = function (event) { d.tier = event.target.value; Hub.save(); refreshEstimate(); };
      var pickAddon = function (key) {
        return function (event) { d.addons[key] = event.target.checked; Hub.save(); refreshEstimate(); };
      };
      Hub.fill(calcBody, [
        h("fieldset", {}, [
          h("legend", { text: "Plan" }),
          Hub.choice({ id: "plan-basic", name: "plan_tier", kind: "radio", value: "basic",
                       label: "Basic — $6.00 per seat each month", checked: d.tier === "basic",
                       onchange: pickTier }),
          Hub.choice({ id: "plan-team", name: "plan_tier", kind: "radio", value: "team",
                       label: "Team — $10.00 per seat each month", checked: d.tier === "team",
                       onchange: pickTier }),
          Hub.choice({ id: "plan-scale", name: "plan_tier", kind: "radio", value: "scale",
                       label: "Scale — $18.00 per seat each month", checked: d.tier === "scale",
                       onchange: pickTier })
        ]),
        Hub.field({ id: "plan-seats", name: "seats", type: "number", label: "Seats",
                    value: d.seats, hint: "Between 1 and 250, and at least one per person.",
                    oninput: function (event) {
                      d.seats = event.target.value; Hub.save(); refreshEstimate();
                    } }),
        Hub.picker({ id: "billing-period", name: "billing_period", label: "Billing period",
                     options: [["1", "Monthly"], ["3", "Quarterly"],
                               ["12", "Yearly (10% off seat charges)"]],
                     value: d.period,
                     onchange: function (event) {
                       d.period = event.target.value; Hub.save(); refreshEstimate();
                     } }),
        h("fieldset", {}, [
          h("legend", { text: "Add-ons" }),
          Hub.choice({ id: "addon-support", name: "addon_support", kind: "checkbox",
                       label: "Priority support — $2.00 per seat each month",
                       checked: d.addons.support, onchange: pickAddon("support") }),
          Hub.choice({ id: "addon-history", name: "addon_history", kind: "checkbox",
                       label: "Extended version history — $30.00 each month",
                       checked: d.addons.history, onchange: pickAddon("history") }),
          Hub.choice({ id: "addon-audit", name: "addon_audit", kind: "checkbox",
                       label: "Audit log export — $45.00 each month",
                       checked: d.addons.audit, onchange: pickAddon("audit") })
        ]),
        h("h3", { text: "Estimate" }),
        estimate,
        Hub.note(calcProblem, "bad"),
        h("div", { "class": "actions" }, [
          Hub.button("plan-review", "Review plan change", function () {
            var read = readDraft();
            if (read.problem) {
              calcProblem = read.problem;
            } else if (sameAsCurrent(read.config)) {
              calcProblem = "Nothing to change: this matches your current plan.";
            } else {
              calcProblem = ""; authorised = false; stage = "review";
            }
            renderCalculator();
          }),
          Hub.button("plan-close", "Close calculator", function () {
            stage = "closed"; calcProblem = ""; renderCalculator();
          }, "ghost")
        ])
      ]);
      return;
    }

    var read = readDraft();
    if (read.problem) { stage = "edit"; calcProblem = read.problem; renderCalculator(); return; }
    var config = read.config, cost = Hub.quote(config), before = state.plan;
    Hub.fill(calcBody, [
      h("div", { "class": "guard calm" }, [
        h("h3", { text: "Check the change before it takes effect" }),
        Hub.facts([
          Hub.fact("Plan", Hub.PLANS[before.tier].label + " → " + Hub.PLANS[config.tier].label),
          Hub.fact("Seats", before.seats + " → " + config.seats),
          Hub.fact("Billed", Hub.PERIODS[String(before.period)] + " → " +
                   Hub.PERIODS[String(config.period)]),
          Hub.fact("Add-ons", addonNames(before.addons) + " → " + addonNames(config.addons)),
          Hub.fact("New recurring charge", Hub.money(cost.total) + " " +
                   periodPhrase(config.period), "review-recurring-charge", "grand")
        ]),
        Hub.choice({ id: "authorise-charge", name: "authorise_charge", kind: "checkbox",
                     label: "I authorise Tidepool to charge the saved card this amount " +
                            periodPhrase(config.period) + ".",
                     checked: authorised,
                     onchange: function (event) { authorised = event.target.checked; } }),
        Hub.note(calcProblem, "bad"),
        h("div", { "class": "actions" }, [
          Hub.button("plan-apply", "Apply plan change", function () {
            if (!authorised) {
              calcProblem = "Tick the authorisation before applying the change.";
              renderCalculator();
              return;
            }
            state.plan = config;
            state.lastPlanChange = { config: config, cost: cost };
            Hub.save();
            stage = "closed"; calcProblem = ""; authorised = false;
            renderCurrent();
            renderCalculator();
          }),
          Hub.button("plan-back", "Back to editing", function () {
            stage = "edit"; calcProblem = ""; renderCalculator();
          }, "ghost")
        ])
      ])
    ]);
  }

  /* ---- invoices: a switch that reveals a dependent address ------------------------------ */
  var invoiceBody = document.getElementById("invoice-body");
  var financeValue = null, financeProblem = "", financeNote = "";

  function renderInvoices() {
    if (financeValue === null) { financeValue = state.billing.financeEmail; }
    Hub.fill(invoiceBody, [
      h("p", { id: "invoice-recipient", text: "Invoices go to " + state.profile.email + "." }),
      Hub.choice({ id: "invoice-copy", name: "invoice_copy", kind: "switch",
                   label: "Also send a copy to a finance address",
                   checked: state.billing.invoiceCopy,
                   onchange: function (event) {
                     state.billing.invoiceCopy = event.target.checked; Hub.save();
                     financeProblem = ""; financeNote = ""; renderInvoices();
                   } }),
      state.billing.invoiceCopy ? h("div", { "class": "dependents" }, [
        Hub.field({ id: "finance-email", name: "finance_email", type: "email",
                    label: "Finance address", value: financeValue,
                    oninput: function (event) { financeValue = event.target.value; } }),
        h("p", { id: "finance-current", text: state.billing.financeEmail ?
                 "Copies go to " + state.billing.financeEmail + "." :
                 "No finance address is set yet, so no copies are sent." }),
        Hub.note(financeProblem, "bad"),
        Hub.note(financeNote, "good"),
        h("div", { "class": "actions" }, [
          Hub.button("finance-apply", "Apply finance address", function () {
            if (!Hub.isEmail(financeValue)) {
              financeProblem = "Enter a full email address for the finance copy.";
              financeNote = "";
            } else {
              state.billing.financeEmail = financeValue; Hub.save();
              financeProblem = ""; financeNote = "Finance address applied.";
            }
            renderInvoices();
          })
        ])
      ]) : null
    ]);
  }

  /* ---- billing details: inline edit ----------------------------------------------------- */
  var detailsBody = document.getElementById("details-body");
  var detailsEditing = false, detailsName = "", detailsCountry = "", detailsProblem = "",
      detailsNote = "";
  var COUNTRIES = [["GB", "United Kingdom"], ["IE", "Ireland"], ["NL", "Netherlands"],
                   ["DE", "Germany"], ["US", "United States"]];

  function countryLabel(code) {
    return COUNTRIES.filter(function (pair) { return pair[0] === code; })[0][1];
  }

  function renderDetails() {
    if (!detailsEditing) {
      Hub.fill(detailsBody, [
        Hub.facts([
          Hub.fact("Name on invoices", state.billing.billingName, "billing-name-current"),
          Hub.fact("Country", countryLabel(state.billing.country), "billing-country-current"),
          Hub.fact("Card", "Visa ending 4417")
        ]),
        h("div", { "class": "actions" }, [
          Hub.button("details-edit", "Edit billing details", function () {
            detailsEditing = true; detailsName = state.billing.billingName;
            detailsCountry = state.billing.country; detailsProblem = ""; detailsNote = "";
            renderDetails();
          }, "ghost")
        ]),
        Hub.note(detailsNote, "good")
      ]);
      return;
    }
    Hub.fill(detailsBody, [
      Hub.field({ id: "billing-name", name: "billing_name", label: "Name on invoices",
                  value: detailsName, hint: "Between 2 and 80 characters.",
                  oninput: function (event) { detailsName = event.target.value; } }),
      Hub.picker({ id: "billing-country", name: "billing_country", label: "Country",
                   options: COUNTRIES, value: detailsCountry,
                   onchange: function (event) { detailsCountry = event.target.value; } }),
      Hub.note(detailsProblem, "bad"),
      h("div", { "class": "actions" }, [
        Hub.button("details-apply", "Apply billing details", function () {
          var cleaned = detailsName.replace(/\s+/g, " ").replace(/^ | $/g, "");
          if (cleaned.length < 2 || cleaned.length > 80) {
            detailsProblem = "The name on invoices needs between 2 and 80 characters.";
            renderDetails();
            return;
          }
          state.billing.billingName = cleaned; state.billing.country = detailsCountry;
          Hub.save();
          detailsEditing = false; detailsNote = "Billing details applied.";
          renderDetails();
        }),
        Hub.button("details-discard", "Discard", function () {
          detailsEditing = false; detailsNote = ""; renderDetails();
        }, "ghost")
      ])
    ]);
  }

  Hub.start();
  renderCurrent();
  renderCalculator();
  renderInvoices();
  renderDetails();
})();
