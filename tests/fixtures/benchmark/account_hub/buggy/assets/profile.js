/* Profile page: four independent panels. Each keeps its own in-progress edit in memory and
   re-renders only itself, so an edit under way in one panel is untouched by another. */
(function () {
  "use strict";
  var h = Hub.h, state = Hub.state();

  /* ---- display name: inline edit, apply or discard ----------------------------------- */
  var nameBody = document.getElementById("name-body");
  var nameEditing = false, nameValue = "", nameProblem = "", nameNote = "";

  function renderName() {
    if (!nameEditing) {
      Hub.fill(nameBody, [
        Hub.facts([Hub.fact("Shown to teammates as", state.profile.displayName, "name-current")]),
        h("div", { "class": "actions" }, [
          Hub.button("name-edit", "Edit display name", function () {
            nameEditing = true; nameValue = state.profile.displayName; nameProblem = "";
            nameNote = ""; renderName();
          }, "ghost")
        ]),
        Hub.note(nameNote, "good")
      ]);
      return;
    }
    Hub.fill(nameBody, [
      Hub.field({ id: "display-name", name: "display_name", label: "Display name",
                  value: nameValue, hint: "Between 1 and 60 characters.",
                  oninput: function (event) { nameValue = event.target.value; } }),
      Hub.note(nameProblem, "bad"),
      h("div", { "class": "actions" }, [
        Hub.button("name-apply", "Apply name", function () {
          var cleaned = nameValue.replace(/\s+/g, " ").replace(/^ | $/g, "");
          if (cleaned.length < 1 || cleaned.length > 60) {
            nameProblem = "A display name needs between 1 and 60 characters.";
            renderName();
            return;
          }
          state.profile.displayName = cleaned;
          Hub.save();
          nameEditing = false; nameNote = "Display name updated.";
          Hub.renderChrome();
          renderName();
        }),
        Hub.button("name-discard", "Discard", function () {
          nameEditing = false; nameNote = ""; renderName();
        }, "ghost")
      ])
    ]);
  }

  /* ---- contact email: edit, review, then a deliberate second step ---------------------
     The success note is cleared whenever the editor is opened again, and the editor's
     fields are removed while the change is under review, so the note always describes
     the addresses that were last typed. */
  var emailBody = document.getElementById("email-body");
  var emailStage = "view";            /* view | edit | review */
  var newValue = "", repeatValue = "", pendingEmail = "", accessAcknowledged = false;
  var emailProblem = "", switchedTo = "";

  function renderEmail() {
    if (emailStage === "view") {
      Hub.fill(emailBody, [
        Hub.facts([Hub.fact("Contact email", state.profile.email, "email-current")]),
        h("div", { "class": "actions" }, [
          Hub.button("email-edit", "Change contact email", function () {
            emailStage = "edit"; newValue = ""; repeatValue = ""; emailProblem = "";
            switchedTo = ""; renderEmail();
          }, "ghost")
        ]),
        switchedTo ? h("p", { "class": "note good" }, [
          "Contact email switched to ",
          h("strong", { id: "email-switch-result", text: switchedTo }),
          ". Sign-in notices and invoices now go there."
        ]) : null
      ]);
      return;
    }
    if (emailStage === "edit") {
      Hub.fill(emailBody, [
        h("p", { text: "Current address: " + state.profile.email }),
        Hub.field({ id: "new-email", name: "new_email", type: "email", label: "New contact email",
                    value: newValue,
                    oninput: function (event) { newValue = event.target.value; } }),
        Hub.field({ id: "repeat-email", name: "repeat_email", type: "email",
                    label: "Repeat the new address", value: repeatValue,
                    hint: "Both addresses must match exactly before the change can be reviewed.",
                    oninput: function (event) { repeatValue = event.target.value; } }),
        Hub.note(emailProblem, "bad"),
        h("div", { "class": "actions" }, [
          Hub.button("email-review", "Review email change", reviewEmail),
          Hub.button("email-discard", "Discard", function () {
            emailStage = "view"; newValue = ""; repeatValue = ""; emailProblem = "";
            renderEmail();
          }, "ghost")
        ])
      ]);
      return;
    }
    Hub.fill(emailBody, [
      h("div", { "class": "guard calm" }, [
        h("p", { text: "Your contact email will change from " + state.profile.email + " to " +
                       pendingEmail + "." }),
        Hub.choice({ id: "ack-email-access", name: "ack_email_access", kind: "checkbox",
                     label: "I can read mail sent to the new address.",
                     checked: accessAcknowledged,
                     onchange: function (event) { accessAcknowledged = event.target.checked; } }),
        Hub.note(emailProblem, "bad"),
        h("div", { "class": "actions" }, [
          Hub.button("email-switch", "Switch to this address", function () {
            if (!accessAcknowledged) {
              emailProblem = "Tick the box to say you can read mail at the new address.";
              renderEmail();
              return;
            }
            state.profile.email = pendingEmail;
            Hub.save();
            switchedTo = pendingEmail;
            emailStage = "view"; newValue = ""; repeatValue = ""; pendingEmail = "";
            emailProblem = "";
            Hub.renderChrome();
            renderEmail();
          }),
          Hub.button("email-back", "Back to editing", function () {
            emailStage = "edit"; emailProblem = ""; renderEmail();
          }, "ghost")
        ])
      ])
    ]);
  }

  function reviewEmail() {
    var addressesMatch = repeatValue !== "";
    if (!Hub.isEmail(newValue)) {
      emailProblem = "Enter a full email address, such as name@example.com.";
    } else if (newValue === state.profile.email) {
      emailProblem = "That is already your contact email.";
    } else if (!addressesMatch) {
      emailProblem = "The two addresses do not match. Repeat the new address exactly.";
    } else {
      emailProblem = "";
      pendingEmail = newValue;
      accessAcknowledged = false;
      emailStage = "review";
    }
    renderEmail();
  }

  /* ---- language and region: pending changes reveal apply / discard -------------------- */
  var regionBody = document.getElementById("region-body");
  var regionDraft = null, regionNote = "";
  var LANGUAGES = [["en-GB", "English (United Kingdom)"], ["en-US", "English (United States)"],
                   ["de-DE", "Deutsch"], ["fr-FR", "Français"], ["es-ES", "Español"]];
  var ZONES = [["Europe/London", "London (UTC+0)"], ["Europe/Berlin", "Berlin (UTC+1)"],
               ["America/New_York", "New York (UTC-5)"], ["Asia/Singapore", "Singapore (UTC+8)"]];

  function regionSaved() {
    return { language: state.profile.language, timeZone: state.profile.timeZone,
             timeFormat: state.profile.timeFormat };
  }

  function regionChanged() {
    var saved = regionSaved();
    return regionDraft.language !== saved.language || regionDraft.timeZone !== saved.timeZone ||
      regionDraft.timeFormat !== saved.timeFormat;
  }

  function labelOf(pairs, value) {
    var found = pairs.filter(function (pair) { return pair[0] === value; })[0];
    return found ? found[1] : value;
  }

  function renderRegion() {
    if (!regionDraft) { regionDraft = regionSaved(); }
    function setDraft(key) {
      return function (event) {
        regionDraft[key] = event.target.value; regionNote = ""; renderRegion();
      };
    }
    var pending = regionChanged();
    Hub.fill(regionBody, [
      Hub.picker({ id: "language", name: "language", label: "Language", options: LANGUAGES,
                   value: regionDraft.language, onchange: setDraft("language") }),
      Hub.picker({ id: "time-zone", name: "time_zone", label: "Time zone", options: ZONES,
                   value: regionDraft.timeZone, onchange: setDraft("timeZone") }),
      h("fieldset", {}, [
        h("legend", { text: "Clock" }),
        Hub.choice({ id: "time-format-12", name: "time_format", kind: "radio", value: "12",
                     label: "12-hour (2:30 pm)", checked: regionDraft.timeFormat === "12",
                     onchange: setDraft("timeFormat") }),
        Hub.choice({ id: "time-format-24", name: "time_format", kind: "radio", value: "24",
                     label: "24-hour (14:30)", checked: regionDraft.timeFormat === "24",
                     onchange: setDraft("timeFormat") })
      ]),
      h("p", { id: "region-current", text: "In use now: " +
               labelOf(LANGUAGES, state.profile.language) + ", " +
               labelOf(ZONES, state.profile.timeZone) + ", " +
               state.profile.timeFormat + "-hour clock." }),
      pending ? Hub.note("You have regional changes that are not applied yet.", "plain") : null,
      pending ? h("div", { "class": "actions" }, [
        Hub.button("region-apply", "Apply regional settings", function () {
          state.profile.language = regionDraft.language;
          state.profile.timeZone = regionDraft.timeZone;
          state.profile.timeFormat = regionDraft.timeFormat;
          Hub.save();
          regionNote = "Regional settings applied.";
          renderRegion();
        }),
        Hub.button("region-discard", "Discard changes", function () {
          regionDraft = regionSaved(); regionNote = ""; renderRegion();
        }, "ghost")
      ]) : null,
      Hub.note(regionNote, "good")
    ]);
  }

  /* ---- directory listing: a switch that reveals dependent options --------------------- */
  var directoryBody = document.getElementById("directory-body");

  function renderDirectory() {
    function toggle(key) {
      return function (event) {
        state.profile[key] = event.target.checked; Hub.save(); renderDirectory();
      };
    }
    var shown = ["name"];
    if (state.profile.showEmail) { shown.push("contact email"); }
    if (state.profile.showRole) { shown.push("role"); }
    Hub.fill(directoryBody, [
      Hub.choice({ id: "directory-listed", name: "directory_listed", kind: "switch",
                   label: "List me in the workspace directory", checked: state.profile.listed,
                   onchange: toggle("listed") }),
      state.profile.listed ? h("div", { "class": "dependents" }, [
        Hub.choice({ id: "directory-show-email", name: "directory_show_email", kind: "checkbox",
                     label: "Show my contact email on my directory card",
                     checked: state.profile.showEmail, onchange: toggle("showEmail") }),
        Hub.choice({ id: "directory-show-role", name: "directory_show_role", kind: "checkbox",
                     label: "Show my role on my directory card",
                     checked: state.profile.showRole, onchange: toggle("showRole") })
      ]) : null,
      h("p", { id: "directory-summary", text: state.profile.listed ?
               "Your directory card shows: " + shown.join(", ") + "." :
               "You are hidden from the workspace directory." })
    ]);
  }

  Hub.start();
  renderName();
  renderEmail();
  renderRegion();
  renderDirectory();
})();
