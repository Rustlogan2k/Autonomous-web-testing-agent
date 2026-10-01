/* Overview page: a read-only digest of every section, rebuilt from the shared store. */
(function () {
  "use strict";
  var h = Hub.h, state = Hub.state();

  var tipBody = document.getElementById("tip-body");
  function renderTip() {
    if (state.tipDismissed) { Hub.fill(tipBody, []); return; }
    Hub.fill(tipBody, [h("section", { "class": "panel" }, [
      h("h2", { text: "Tip" }),
      h("p", { text: "Every section saves on its own. Changes you apply on one page never " +
                     "wait for another." }),
      h("div", { "class": "actions" }, [
        Hub.button("tip-dismiss", "Dismiss tip", function () {
          state.tipDismissed = true; Hub.save(); renderTip();
        }, "ghost")
      ])
    ])]);
  }

  function card(title, lines, linkId, href, linkText) {
    return h("section", { "class": "panel" }, [
      h("h2", { text: title })
    ].concat(lines.map(function (line) { return h("p", { text: line }); })).concat([
      h("p", {}, [h("a", { id: linkId, href: href, text: linkText })])
    ]));
  }

  function renderCards() {
    var plan = state.plan, n = state.notifications, sec = state.security;
    var periodWord = { "1": "month", "3": "quarter", "12": "year" }[String(plan.period)];
    Hub.fill(document.getElementById("cards-body"), [h("div", { "class": "cards" }, [
      card("Profile", [state.profile.displayName, state.profile.email],
           "card-profile", "profile.html", "Edit profile"),
      card("Plan", [Hub.PLANS[plan.tier].label + " plan, " + plan.seats + " seats",
                    Hub.money(Hub.quote(plan).total) + " per " + periodWord],
           "card-billing", "billing.html", "Manage plan"),
      card("Team", [(state.team.members.length + 1) + " members, " + state.team.invites.length +
                    " invites waiting"], "card-team", "team.html", "Manage team"),
      card("Security", ["Two-step verification " + (sec.twoStep ? "on" : "off"),
                        sec.sessions.length + " other devices signed in"],
           "card-security", "security.html", "Review security"),
      card("Notifications", [n.pausedFor ? "Paused" : (n.emailEnabled ? "Email on" : "Email off") +
                             ", phone " + (n.pushEnabled ? "on" : "off")],
           "card-notifications", "notifications.html", "Adjust notifications"),
      card("Data & privacy", [state.privacy.closure ? "Workspace closure scheduled" :
                              "Trash kept " + state.privacy.retention + " days"],
           "card-privacy", "privacy.html", "Manage data")
    ])]);
  }

  Hub.start(renderCards);
  renderTip();
  renderCards();
})();
