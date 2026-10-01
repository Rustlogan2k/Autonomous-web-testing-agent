/* Data & privacy page: sharing switches, trash retention, data export, and closing the
   workspace, which needs a reason, two acknowledgements and the account password. */
(function () {
  "use strict";
  var h = Hub.h, state = Hub.state(), p = state.privacy;

  /* ---- sharing ------------------------------------------------------------------------------ */
  var sharingBody = document.getElementById("sharing-body");

  function renderSharing() {
    function toggle(key) {
      return function (event) { p[key] = event.target.checked; Hub.save(); renderSharing(); };
    }
    Hub.fill(sharingBody, [
      Hub.choice({ id: "share-usage", name: "share_usage", kind: "switch",
                   label: "Share anonymous usage statistics", checked: p.shareUsage,
                   onchange: toggle("shareUsage") }),
      Hub.choice({ id: "personal-tips", name: "personal_tips", kind: "switch",
                   label: "Suggest tips based on how I use Tidepool", checked: p.personalTips,
                   onchange: toggle("personalTips") }),
      h("p", { id: "sharing-summary", text: (p.shareUsage ? "Usage statistics are shared" :
               "Usage statistics are not shared") + "; " +
               (p.personalTips ? "tips are personalised." : "tips are general.") })
    ]);
  }

  /* ---- trash retention ------------------------------------------------------------------------ */
  var retentionBody = document.getElementById("retention-body");

  function renderRetention() {
    var radio = function (days) {
      return Hub.choice({ id: "retention-" + days, name: "trash_retention", kind: "radio",
                          value: days, label: days + " days", checked: p.retention === days,
                          onchange: function (event) {
                            p.retention = event.target.value; Hub.save(); renderRetention();
                          } });
    };
    Hub.fill(retentionBody, [
      h("fieldset", {}, [h("legend", { text: "Keep deleted files for" }),
                         radio("30"), radio("60"), radio("90")]),
      h("p", { id: "retention-current", text: "Deleted files are erased after " + p.retention +
               " days." })
    ]);
  }

  /* ---- export ---------------------------------------------------------------------------------- */
  var exportBody = document.getElementById("export-body");
  var exportParts = { files: true, activity: false, members: false }, exportFormat = "zip",
      exportProblem = "";
  var PART_LABELS = { files: "files", activity: "activity history", members: "member list" };

  function renderExport() {
    if (p.exportRequest) {
      var parts = Object.keys(p.exportRequest.parts).filter(function (key) {
        return p.exportRequest.parts[key];
      }).map(function (key) { return PART_LABELS[key]; });
      Hub.fill(exportBody, [
        h("p", { id: "export-status", text: "Export requested: " + parts.join(", ") + " as " +
                 p.exportRequest.format.toUpperCase() + ". A download link will be sent to " +
                 state.profile.email + "." }),
        h("div", { "class": "actions" }, [
          Hub.button("export-withdraw", "Withdraw export request", function () {
            p.exportRequest = null; Hub.save(); renderExport();
          }, "ghost")
        ])
      ]);
      return;
    }
    var part = function (key, label) {
      return Hub.choice({ id: "export-" + key, name: "export_" + key, kind: "checkbox",
                          label: label, checked: exportParts[key],
                          onchange: function (event) { exportParts[key] = event.target.checked; } });
    };
    Hub.fill(exportBody, [
      h("fieldset", {}, [h("legend", { text: "Include" }),
                         part("files", "Files and folders"),
                         part("activity", "Activity history"),
                         part("members", "Member list")]),
      Hub.picker({ id: "export-format", name: "export_format", label: "Archive format",
                   options: [["zip", "ZIP"], ["tar", "TAR"]], value: exportFormat,
                   onchange: function (event) { exportFormat = event.target.value; } }),
      Hub.note(exportProblem, "bad"),
      h("div", { "class": "actions" }, [
        Hub.button("export-request", "Request export", function () {
          if (!exportParts.files && !exportParts.activity && !exportParts.members) {
            exportProblem = "Choose at least one thing to include.";
            renderExport();
            return;
          }
          p.exportRequest = { parts: { files: exportParts.files, activity: exportParts.activity,
                                       members: exportParts.members }, format: exportFormat };
          Hub.save(); exportProblem = ""; renderExport();
        })
      ])
    ]);
  }

  /* ---- close the workspace ---------------------------------------------------------------------- */
  var closureBody = document.getElementById("closure-body");
  var closing = false, reason = "", ackErase = false, ackMembers = false, password = "",
      closureProblem = "";
  var REASONS = [["", "Choose a reason"], ["cost", "It costs too much"],
                 ["switching", "We are moving to another service"],
                 ["project-ended", "The project has ended"], ["other", "Something else"]];

  function reasonLabel(value) {
    return REASONS.filter(function (pair) { return pair[0] === value; })[0][1];
  }

  function renderClosure() {
    if (p.closure) {
      Hub.fill(closureBody, [
        h("p", { id: "closure-status", text: "Closure is scheduled (" +
                 reasonLabel(p.closure.reason).toLowerCase() + "). Use Reopen workspace at " +
                 "the top of the page to undo it." })
      ]);
      return;
    }
    if (!closing) {
      Hub.fill(closureBody, [
        h("div", { "class": "actions" }, [
          Hub.button("closure-open", "Close workspace…", function () {
            closing = true; reason = ""; ackErase = false; ackMembers = false; password = "";
            closureProblem = ""; renderClosure();
          }, "risky")
        ])
      ]);
      return;
    }
    Hub.fill(closureBody, [
      h("div", { "class": "guard" }, [
        h("p", { text: "Closing " + state.workspace + " signs everyone out and erases every " +
                       "file after 30 days. It needs a reason, both acknowledgements and your " +
                       "password." }),
        Hub.picker({ id: "closure-reason", name: "closure_reason", label: "Why are you closing it?",
                     options: REASONS, value: reason,
                     onchange: function (event) { reason = event.target.value; } }),
        Hub.choice({ id: "ack-closure-erase", name: "ack_closure_erase", kind: "checkbox",
                     label: "I understand every file is erased after 30 days.", checked: ackErase,
                     onchange: function (event) { ackErase = event.target.checked; } }),
        Hub.choice({ id: "ack-closure-members", name: "ack_closure_members", kind: "checkbox",
                     label: "I understand all " + (state.team.members.length + 1) +
                            " members lose access.", checked: ackMembers,
                     onchange: function (event) { ackMembers = event.target.checked; } }),
        Hub.field({ id: "closure-password", name: "closure_password", type: "password",
                    label: "Your password", value: password,
                    oninput: function (event) { password = event.target.value; } }),
        Hub.note(closureProblem, "bad"),
        h("div", { "class": "actions" }, [
          Hub.button("closure-final", "Close workspace permanently", function () {
            if (!reason) {
              closureProblem = "Choose a reason for closing.";
            } else if (!ackErase || !ackMembers) {
              closureProblem = "Tick both acknowledgements.";
            } else if (!password.trim()) {
              closureProblem = "Enter your password.";
            } else {
              p.closure = { reason: reason }; Hub.save();
              closing = false; closureProblem = "";
              Hub.renderChrome();
            }
            renderClosure();
          }, "risky"),
          Hub.button("closure-keep", "Keep workspace open", function () {
            closing = false; renderClosure();
          }, "ghost")
        ])
      ])
    ]);
  }

  Hub.start(renderClosure);
  renderSharing();
  renderRetention();
  renderExport();
  renderClosure();
})();
