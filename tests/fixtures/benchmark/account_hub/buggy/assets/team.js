/* Team & seats page. Every member, and every invite still pending, holds a seat; an invite
   needs a free seat, so the plan calculator and this page depend on each other. */
(function () {
  "use strict";
  var h = Hub.h, state = Hub.state();
  var ROLES = [["admin", "Admin"], ["editor", "Editor"], ["viewer", "Viewer"]];

  function roleLabel(role) {
    return ROLES.filter(function (pair) { return pair[0] === role; })[0][1];
  }

  function freeSeats() { return state.plan.seats - Hub.peopleInUse(); }

  /* ---- seat usage ------------------------------------------------------------------------ */
  var seatsBody = document.getElementById("seats-body");

  function renderSeats() {
    Hub.fill(seatsBody, [
      Hub.facts([
        Hub.fact("Seats on the plan", String(state.plan.seats), "seat-total"),
        Hub.fact("Holding a seat", Hub.peopleInUse() + " (members and pending invites)",
                 "seat-used"),
        Hub.fact("Free seats", String(Math.max(freeSeats(), 0)), "seat-free")
      ]),
      h("p", {}, [
        "Need more room? ",
        h("a", { href: "billing.html", id: "seats-to-billing", text: "Add seats in Plan & billing" }),
        "."
      ])
    ]);
  }

  /* ---- members: role changes apply at once, removal needs a second step ----------------- */
  var membersBody = document.getElementById("members-body");
  var removing = null, transferTo = "owner", removeAck = false, removeProblem = "",
      memberNote = "";

  function member(id) {
    return state.team.members.filter(function (m) { return m.id === id; })[0];
  }

  function renderMembers() {
    var rows = [h("li", {}, [
      h("div", { "class": "who" }, [
        h("strong", { text: state.profile.displayName + " (you)" }),
        h("span", { text: state.profile.email + " · Owner" })
      ])
    ])];
    state.team.members.forEach(function (m) {
      var first = m.name.split(" ")[0];
      rows.push(h("li", {}, [
        h("div", { "class": "who" }, [
          h("strong", { text: m.name }),
          h("span", { text: m.email + " · " + roleLabel(m.role) })
        ]),
        Hub.picker({ id: "role-" + m.id, name: "role_" + m.id, label: "Role for " + first,
                     options: ROLES, value: m.role,
                     onchange: function (event) {
                       m.role = event.target.value; Hub.save();
                       memberNote = m.name + " is now " + roleLabel(m.role) + ".";
                       renderMembers();
                     } }),
        Hub.button("remove-" + m.id, "Remove " + first, function () {
          removing = m.id; transferTo = "owner"; removeAck = false; removeProblem = "";
          memberNote = ""; renderMembers();
        }, "ghost")
      ]));
    });

    var guard = null;
    var target = removing ? member(removing) : null;
    if (target) {
      var others = [["owner", state.profile.displayName + " (you)"]].concat(
        state.team.members.filter(function (m) { return m.id !== target.id; })
          .map(function (m) { return [m.id, m.name]; }));
      guard = h("div", { "class": "guard" }, [
        h("p", { text: "Remove " + target.name + " from " + state.workspace + "? They lose " +
                       "access straight away and their seat becomes free." }),
        Hub.picker({ id: "transfer-files-to", name: "transfer_files_to",
                     label: "Give their private files to", options: others, value: transferTo,
                     onchange: function (event) { transferTo = event.target.value; } }),
        Hub.choice({ id: "ack-remove-member", name: "ack_remove_member", kind: "checkbox",
                     label: "I understand " + target.name.split(" ")[0] +
                            " can no longer open any shared file.",
                     checked: removeAck,
                     onchange: function (event) { removeAck = event.target.checked; } }),
        Hub.note(removeProblem, "bad"),
        h("div", { "class": "actions" }, [
          Hub.button("remove-final", "Remove from workspace", function () {
            if (!removeAck) {
              removeProblem = "Tick the box to show you understand the loss of access.";
              renderMembers();
              return;
            }
            var heir = transferTo === "owner" ? state.profile.displayName :
              (member(transferTo) || {}).name;
            state.team.members = state.team.members.filter(function (m) {
              return m.id !== target.id;
            });
            Hub.save();
            memberNote = target.name + " was removed. Their files now belong to " + heir + ".";
            removing = null;
            renderAll();
          }, "risky"),
          Hub.button("remove-keep", "Keep member", function () {
            removing = null; renderMembers();
          }, "ghost")
        ])
      ]);
    }
    Hub.fill(membersBody, [h("ul", { "class": "roster" }, rows), guard,
                           Hub.note(memberNote, "good")]);
  }

  /* ---- invites --------------------------------------------------------------------------- */
  var inviteBody = document.getElementById("invite-body");
  var inviting = false, inviteEmail = "", inviteRole = "editor", inviteProblem = "",
      inviteNote = "";

  function renderInvites() {
    var pending = state.team.invites.map(function (invite, index) {
      return h("li", {}, [
        h("div", { "class": "who" }, [
          h("strong", { text: invite.email }),
          h("span", { text: "Invited as " + roleLabel(invite.role) + " · waiting for a reply" })
        ]),
        Hub.button("revoke-" + (index + 1), "Revoke invite " + (index + 1), function () {
          state.team.invites.splice(index, 1); Hub.save();
          inviteNote = "Invite revoked."; renderAll();
        }, "ghost")
      ]);
    });
    var kids = [
      pending.length ? h("ul", { "class": "roster" }, pending) :
        h("p", { text: "No invites are waiting for a reply." })
    ];
    if (!inviting) {
      kids.push(h("div", { "class": "actions" }, [
        Hub.button("invite-open", "Invite a teammate", function () {
          inviting = true; inviteEmail = ""; inviteRole = "editor"; inviteProblem = "";
          inviteNote = ""; renderInvites();
        })
      ]));
    } else {
      kids.push(h("div", { "class": "guard calm" }, [
        Hub.field({ id: "invite-email", name: "invite_email", type: "email",
                    label: "Teammate's email", value: inviteEmail,
                    oninput: function (event) { inviteEmail = event.target.value; } }),
        Hub.picker({ id: "invite-role", name: "invite_role", label: "Role", options: ROLES,
                     value: inviteRole,
                     onchange: function (event) { inviteRole = event.target.value; } }),
        Hub.note(inviteProblem, "bad"),
        h("div", { "class": "actions" }, [
          Hub.button("invite-send", "Send invite", sendInvite),
          Hub.button("invite-discard", "Discard", function () {
            inviting = false; renderInvites();
          }, "ghost")
        ])
      ]));
    }
    kids.push(Hub.note(inviteNote, "good"));
    Hub.fill(inviteBody, kids);
  }

  function sendInvite() {
    var address = inviteEmail.toLowerCase();
    var taken = address === state.profile.email.toLowerCase() ||
      state.team.members.some(function (m) { return m.email === address; }) ||
      state.team.invites.some(function (i) { return i.email === address; });
    if (!Hub.isEmail(inviteEmail)) {
      inviteProblem = "Enter a full email address, such as name@example.com.";
    } else if (taken) {
      inviteProblem = "That person is already in the workspace or already invited.";
    } else if (freeSeats() < 1) {
      inviteProblem = "All " + state.plan.seats + " seats are taken. Add seats in Plan & " +
                      "billing, or remove someone first.";
    } else {
      state.team.invites.push({ email: address, role: inviteRole });
      Hub.save();
      inviting = false; inviteProblem = "";
      inviteNote = "Invite sent to " + address + ".";
      renderAll();
      return;
    }
    renderInvites();
  }

  function renderAll() {
    renderSeats();
    renderMembers();
    renderInvites();
  }

  Hub.start();
  renderAll();
})();
