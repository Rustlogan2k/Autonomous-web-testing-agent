/* Notifications page: switches apply the moment they change; quiet hours and pausing are
   applied with their own button; restoring defaults needs a second step. */
(function () {
  "use strict";
  var h = Hub.h, state = Hub.state(), n = state.notifications;

  var DEFAULTS = {
    emailEnabled: true, emailMentions: true, emailShares: true, emailComments: false,
    emailBilling: true, digest: "daily", pushEnabled: false, pushMentions: true,
    pushUploads: false, quietEnabled: false, quietFrom: "22", quietUntil: "7",
    newsFeatures: true, newsTips: false, newsResearch: false, pausedFor: ""
  };

  function toggle(key, rerender) {
    return function (event) { n[key] = event.target.checked; Hub.save(); rerender(); };
  }

  /* ---- email ------------------------------------------------------------------------------- */
  var emailBody = document.getElementById("email-alerts-body");

  function renderEmail() {
    var kids = [Hub.choice({ id: "email-enabled", name: "email_enabled", kind: "switch",
                             label: "Send me email notifications", checked: n.emailEnabled,
                             onchange: toggle("emailEnabled", renderEmail) })];
    if (n.emailEnabled) {
      var digestRadio = function (value, label) {
        return Hub.choice({ id: "digest-" + value, name: "email_digest", kind: "radio",
                            value: value, label: label, checked: n.digest === value,
                            onchange: function (event) {
                              n.digest = event.target.value; Hub.save(); renderEmail();
                            } });
      };
      kids.push(h("div", { "class": "dependents" }, [
        Hub.choice({ id: "email-mentions", name: "email_mentions", kind: "checkbox",
                     label: "Someone mentions me", checked: n.emailMentions,
                     onchange: toggle("emailMentions", renderEmail) }),
        Hub.choice({ id: "email-shares", name: "email_shares", kind: "checkbox",
                     label: "A folder is shared with me", checked: n.emailShares,
                     onchange: toggle("emailShares", renderEmail) }),
        Hub.choice({ id: "email-comments", name: "email_comments", kind: "checkbox",
                     label: "New comments on my files", checked: n.emailComments,
                     onchange: toggle("emailComments", renderEmail) }),
        Hub.choice({ id: "email-billing", name: "email_billing", kind: "checkbox",
                     label: "Invoices and payment problems", checked: n.emailBilling,
                     onchange: toggle("emailBilling", renderEmail) }),
        h("fieldset", {}, [
          h("legend", { text: "How often" }),
          digestRadio("instant", "As it happens"),
          digestRadio("daily", "One summary a day"),
          digestRadio("weekly", "One summary a week")
        ])
      ]));
    }
    kids.push(h("p", { id: "email-alerts-summary", text: n.emailEnabled ?
                       "Email is on, " + { instant: "sent as it happens", daily: "as a daily summary",
                                           weekly: "as a weekly summary" }[n.digest] + "." :
                       "Email notifications are off." }));
    Hub.fill(emailBody, kids);
  }

  /* ---- mobile push and quiet hours ------------------------------------------------------------ */
  var pushBody = document.getElementById("push-body");
  var quietDraft = null, quietNote = "";
  var FROM = [["20", "20:00"], ["21", "21:00"], ["22", "22:00"], ["23", "23:00"]];
  var UNTIL = [["6", "06:00"], ["7", "07:00"], ["8", "08:00"], ["9", "09:00"]];

  function hour(value) { return (value.length === 1 ? "0" : "") + value + ":00"; }

  function renderPush() {
    if (!quietDraft) { quietDraft = { from: n.quietFrom, until: n.quietUntil }; }
    var kids = [Hub.choice({ id: "push-enabled", name: "push_enabled", kind: "switch",
                             label: "Send notifications to my phone", checked: n.pushEnabled,
                             onchange: toggle("pushEnabled", renderPush) })];
    if (n.pushEnabled) {
      var dependents = [
        Hub.choice({ id: "push-mentions", name: "push_mentions", kind: "checkbox",
                     label: "Mentions", checked: n.pushMentions,
                     onchange: toggle("pushMentions", renderPush) }),
        Hub.choice({ id: "push-uploads", name: "push_uploads", kind: "checkbox",
                     label: "Uploads to folders I follow", checked: n.pushUploads,
                     onchange: toggle("pushUploads", renderPush) }),
        Hub.choice({ id: "quiet-enabled", name: "quiet_enabled", kind: "switch",
                     label: "Quiet hours", checked: n.quietEnabled,
                     onchange: toggle("quietEnabled", renderPush) })
      ];
      if (n.quietEnabled) {
        dependents.push(h("div", { "class": "dependents" }, [
          Hub.picker({ id: "quiet-from", name: "quiet_from", label: "Quiet from", options: FROM,
                       value: quietDraft.from,
                       onchange: function (event) { quietDraft.from = event.target.value; } }),
          Hub.picker({ id: "quiet-until", name: "quiet_until", label: "Quiet until",
                       options: UNTIL, value: quietDraft.until,
                       onchange: function (event) { quietDraft.until = event.target.value; } }),
          h("div", { "class": "actions" }, [
            Hub.button("quiet-apply", "Apply quiet hours", function () {
              n.quietFrom = quietDraft.from; n.quietUntil = quietDraft.until; Hub.save();
              quietNote = "Quiet hours applied."; renderPush();
            })
          ]),
          h("p", { id: "quiet-current", text: "Quiet from " + hour(n.quietFrom) + " to " +
                                             hour(n.quietUntil) + "." }),
          Hub.note(quietNote, "good")
        ]));
      }
      kids.push(h("div", { "class": "dependents" }, dependents));
    }
    Hub.fill(pushBody, kids);
  }

  /* ---- product news ------------------------------------------------------------------------- */
  var newsBody = document.getElementById("news-body");

  function renderNews() {
    Hub.fill(newsBody, [
      Hub.choice({ id: "news-features", name: "news_features", kind: "switch",
                   label: "New features", checked: n.newsFeatures,
                   onchange: toggle("newsFeatures", renderNews) }),
      Hub.choice({ id: "news-tips", name: "news_tips", kind: "switch",
                   label: "Tips for getting more from Tidepool", checked: n.newsTips,
                   onchange: toggle("newsTips", renderNews) }),
      Hub.choice({ id: "news-research", name: "news_research", kind: "switch",
                   label: "Invitations to user research", checked: n.newsResearch,
                   onchange: toggle("newsResearch", renderNews) })
    ]);
  }

  /* ---- pause ---------------------------------------------------------------------------------- */
  var pauseBody = document.getElementById("pause-body");
  var pauseChoice = "1h";
  var DURATIONS = [["1h", "For 1 hour"], ["8h", "For 8 hours"], ["1d", "Until tomorrow"],
                   ["1w", "For a week"]];

  function durationLabel(value) {
    return DURATIONS.filter(function (pair) { return pair[0] === value; })[0][1].toLowerCase();
  }

  function renderPause() {
    if (n.pausedFor) {
      Hub.fill(pauseBody, [
        h("p", { id: "pause-status", text: "All notifications are paused " +
                 durationLabel(n.pausedFor) + "." }),
        h("div", { "class": "actions" }, [
          Hub.button("pause-resume", "Resume notifications", function () {
            n.pausedFor = ""; Hub.save(); renderPause();
          })
        ])
      ]);
      return;
    }
    Hub.fill(pauseBody, [
      Hub.picker({ id: "pause-duration", name: "pause_duration", label: "Pause everything",
                   options: DURATIONS, value: pauseChoice,
                   onchange: function (event) { pauseChoice = event.target.value; } }),
      h("div", { "class": "actions" }, [
        Hub.button("pause-apply", "Pause notifications", function () {
          n.pausedFor = pauseChoice; Hub.save(); renderPause();
        }, "ghost")
      ])
    ]);
  }

  /* ---- restore defaults ----------------------------------------------------------------------- */
  var restoreBody = document.getElementById("restore-body");
  var askingRestore = false, restoreAck = false, restoreProblem = "", restoreNote = "";

  function renderRestore() {
    if (!askingRestore) {
      Hub.fill(restoreBody, [
        h("div", { "class": "actions" }, [
          Hub.button("restore-open", "Restore default settings…", function () {
            askingRestore = true; restoreAck = false; restoreProblem = ""; restoreNote = "";
            renderRestore();
          }, "ghost")
        ]),
        Hub.note(restoreNote, "good")
      ]);
      return;
    }
    Hub.fill(restoreBody, [
      h("div", { "class": "guard" }, [
        h("p", { text: "Every notification choice on this page goes back to how it was when " +
                       "the account was created." }),
        Hub.choice({ id: "ack-restore-defaults", name: "ack_restore_defaults", kind: "checkbox",
                     label: "Replace my notification choices.", checked: restoreAck,
                     onchange: function (event) { restoreAck = event.target.checked; } }),
        Hub.note(restoreProblem, "bad"),
        h("div", { "class": "actions" }, [
          Hub.button("restore-final", "Restore defaults", function () {
            if (!restoreAck) {
              restoreProblem = "Tick the box to replace your choices."; renderRestore(); return;
            }
            Object.keys(DEFAULTS).forEach(function (key) { n[key] = DEFAULTS[key]; });
            Hub.save();
            askingRestore = false; restoreNote = "Default notification settings restored.";
            quietDraft = null; quietNote = "";
            renderAll();
          }, "risky"),
          Hub.button("restore-keep", "Keep my settings", function () {
            askingRestore = false; renderRestore();
          }, "ghost")
        ])
      ])
    ]);
  }

  function renderAll() {
    renderEmail(); renderPush(); renderNews(); renderPause(); renderRestore();
  }

  Hub.start();
  renderAll();
})();
