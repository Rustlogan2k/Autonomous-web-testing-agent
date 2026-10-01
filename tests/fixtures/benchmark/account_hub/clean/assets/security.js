/* Security page: password change, two-step verification, and signed-in devices. Turning
   protection off, or signing out everywhere, needs a deliberate second step. */
(function () {
  "use strict";
  var h = Hub.h, state = Hub.state();

  /* ---- password -------------------------------------------------------------------------- */
  var passwordBody = document.getElementById("password-body");
  var pwOpen = false, pwCurrent = "", pwNew = "", pwRepeat = "", pwProblem = "", pwNote = "";

  function renderPassword() {
    if (!pwOpen) {
      Hub.fill(passwordBody, [
        Hub.facts([Hub.fact("Password", state.security.passwordChanged ?
                            "Changed during this session" : "Last changed 4 months ago",
                            "password-status")]),
        h("div", { "class": "actions" }, [
          Hub.button("password-open", "Change password", function () {
            pwOpen = true; pwCurrent = ""; pwNew = ""; pwRepeat = ""; pwProblem = "";
            pwNote = ""; renderPassword();
          }, "ghost")
        ]),
        Hub.note(pwNote, "good")
      ]);
      return;
    }
    Hub.fill(passwordBody, [
      Hub.field({ id: "current-password", name: "current_password", type: "password",
                  label: "Current password", value: pwCurrent,
                  oninput: function (event) { pwCurrent = event.target.value; } }),
      Hub.field({ id: "new-password", name: "new_password", type: "password",
                  label: "New password", value: pwNew,
                  hint: "12 to 128 characters, different from the current one.",
                  oninput: function (event) { pwNew = event.target.value; } }),
      Hub.field({ id: "repeat-password", name: "repeat_password", type: "password",
                  label: "Repeat the new password", value: pwRepeat,
                  oninput: function (event) { pwRepeat = event.target.value; } }),
      Hub.note(pwProblem, "bad"),
      h("div", { "class": "actions" }, [
        Hub.button("password-update", "Update password", function () {
          if (!pwCurrent.trim()) {
            pwProblem = "Enter your current password.";
          } else if (pwNew.length < 12 || pwNew.length > 128) {
            pwProblem = "The new password needs 12 to 128 characters.";
          } else if (pwNew === pwCurrent) {
            pwProblem = "Choose a password you are not using now.";
          } else if (pwRepeat !== pwNew) {
            pwProblem = "The repeated password does not match.";
          } else {
            state.security.passwordChanged = true; Hub.save();
            pwOpen = false; pwProblem = "";
            pwNote = "Password updated. Your other devices stay signed in.";
          }
          renderPassword();
        }),
        Hub.button("password-discard", "Discard", function () {
          pwOpen = false; pwNote = ""; renderPassword();
        }, "ghost")
      ])
    ]);
  }

  /* ---- two-step verification --------------------------------------------------------------- */
  var twoStepBody = document.getElementById("two-step-body");
  var askingOff = false, offAck = false, offProblem = "", phoneValue = null, phoneProblem = "",
      twoStepNote = "";
  var METHODS = [["app", "Authenticator app"], ["sms", "Text message"], ["key", "Security key"]];

  function renderTwoStep() {
    var sec = state.security;
    if (phoneValue === null) { phoneValue = sec.phone; }
    var kids = [
      Hub.choice({ id: "two-step-enabled", name: "two_step_enabled", kind: "switch",
                   label: "Ask for a second step when signing in", checked: sec.twoStep,
                   onchange: function (event) {
                     if (event.target.checked) {
                       sec.twoStep = true; Hub.save(); askingOff = false;
                       twoStepNote = "Two-step verification is on.";
                     } else {
                       askingOff = true; offAck = false; offProblem = ""; twoStepNote = "";
                     }
                     renderTwoStep();
                   } })
    ];
    if (sec.twoStep) {
      var methodRadios = METHODS.map(function (pair) {
        return Hub.choice({ id: "two-step-" + pair[0], name: "two_step_method", kind: "radio",
                            value: pair[0], label: pair[1], checked: sec.method === pair[0],
                            onchange: function (event) {
                              sec.method = event.target.value; Hub.save();
                              twoStepNote = "Second step set to " + pair[1].toLowerCase() + ".";
                              renderTwoStep();
                            } });
      });
      var dependents = [h("fieldset", {}, [h("legend", { text: "Second step" })].concat(methodRadios))];
      if (sec.method === "sms") {
        dependents.push(Hub.field({ id: "two-step-phone", name: "two_step_phone", type: "tel",
                                    label: "Mobile number for codes", value: phoneValue,
                                    oninput: function (event) { phoneValue = event.target.value; } }));
        dependents.push(h("p", { id: "two-step-phone-current", text: sec.phone ?
                                 "Codes go to " + sec.phone + "." :
                                 "No mobile number is on file yet." }));
        dependents.push(Hub.note(phoneProblem, "bad"));
        dependents.push(h("div", { "class": "actions" }, [
          Hub.button("phone-apply", "Apply mobile number", function () {
            if (/^\+?[0-9][0-9 \-]{5,19}$/.test(phoneValue)) {
              sec.phone = phoneValue; Hub.save(); phoneProblem = "";
              twoStepNote = "Mobile number applied.";
            } else {
              phoneProblem = "Enter a mobile number of 6 to 20 digits, such as +44 7700 900123.";
            }
            renderTwoStep();
          })
        ]));
      }
      kids.push(h("div", { "class": "dependents" }, dependents));
    }
    if (askingOff && sec.twoStep) {
      kids.push(h("div", { "class": "guard" }, [
        h("p", { text: "Without a second step, anyone with your password can sign in." }),
        Hub.choice({ id: "ack-two-step-off", name: "ack_two_step_off", kind: "checkbox",
                     label: "I accept the weaker protection on this account.", checked: offAck,
                     onchange: function (event) { offAck = event.target.checked; } }),
        Hub.note(offProblem, "bad"),
        h("div", { "class": "actions" }, [
          Hub.button("two-step-off-final", "Turn off two-step verification", function () {
            if (!offAck) {
              offProblem = "Tick the box to accept the weaker protection.";
              renderTwoStep();
              return;
            }
            sec.twoStep = false; Hub.save(); askingOff = false;
            twoStepNote = "Two-step verification is off.";
            renderTwoStep();
          }, "risky"),
          Hub.button("two-step-keep", "Keep it on", function () {
            askingOff = false; renderTwoStep();
          }, "ghost")
        ])
      ]));
    }
    kids.push(Hub.note(twoStepNote, "good"));
    Hub.fill(twoStepBody, kids);
  }

  /* ---- signed-in devices --------------------------------------------------------------------- */
  var devicesBody = document.getElementById("devices-body");
  var askingAll = false, allAck = false, allProblem = "", deviceNote = "";

  function renderDevices() {
    var sec = state.security;
    var rows = [h("li", {}, [h("div", { "class": "who" }, [
      h("strong", { text: "This browser" }), h("span", { text: "Signed in now" })])])];
    sec.sessions.forEach(function (session) {
      rows.push(h("li", {}, [
        h("div", { "class": "who" }, [h("strong", { text: session.label }),
                                       h("span", { text: session.place })]),
        Hub.button("signout-" + session.id, "Sign out " + session.label, function () {
          sec.sessions = sec.sessions.filter(function (s) { return s.id !== session.id; });
          Hub.save(); deviceNote = session.label + " was signed out."; renderDevices();
        }, "ghost")
      ]));
    });
    var kids = [h("ul", { "class": "roster" }, rows)];
    if (sec.sessions.length && !askingAll) {
      kids.push(h("div", { "class": "actions" }, [
        Hub.button("signout-all-open", "Sign out everywhere else…", function () {
          askingAll = true; allAck = false; allProblem = ""; deviceNote = ""; renderDevices();
        }, "ghost")
      ]));
    }
    if (sec.sessions.length && askingAll) {
      kids.push(h("div", { "class": "guard" }, [
        h("p", { text: "Every device except this browser will need to sign in again." }),
        Hub.choice({ id: "ack-signout-all", name: "ack_signout_all", kind: "checkbox",
                     label: "Sign out " + sec.sessions.length + " other devices.", checked: allAck,
                     onchange: function (event) { allAck = event.target.checked; } }),
        Hub.note(allProblem, "bad"),
        h("div", { "class": "actions" }, [
          Hub.button("signout-all-final", "Sign out all other devices", function () {
            if (!allAck) {
              allProblem = "Tick the box first."; renderDevices(); return;
            }
            sec.sessions = []; Hub.save(); askingAll = false;
            deviceNote = "Every other device was signed out.";
            renderDevices();
          }, "risky"),
          Hub.button("signout-all-keep", "Keep devices signed in", function () {
            askingAll = false; renderDevices();
          }, "ghost")
        ])
      ]));
    }
    kids.push(Hub.note(deviceNote, "good"));
    Hub.fill(devicesBody, kids);
  }

  Hub.start();
  renderPassword();
  renderTwoStep();
  renderDevices();
})();
