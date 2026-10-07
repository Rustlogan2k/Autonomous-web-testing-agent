// Sideline's only script: client-side touches that never talk to the server.
//
// * The public league table can be re-sorted by goals for or by points. Sorting only
//   re-orders the rows already on the page: each cell keeps the id of its team's league
//   position (#pts-1 is always the leader's points), and the chosen order is kept in the
//   address (?sort=gf) so a reload shows the same view.
// * "Print layout" restyles the table for paper.
// * "Follow this club" stars a club for as long as the page is open.
(function () {
  "use strict";

  var table = document.getElementById("league-table");
  if (table && table.tBodies.length) {
    var body = table.tBodies[0];
    var buttons = Array.prototype.slice.call(table.querySelectorAll("button[data-sort]"));

    var sortBy = function (key) {
      var rows = Array.prototype.slice.call(body.rows);
      rows.sort(function (a, b) {
        var difference = Number(b.getAttribute("data-" + key)) - Number(a.getAttribute("data-" + key));
        return difference !== 0 ? difference : Number(a.getAttribute("data-pos")) - Number(b.getAttribute("data-pos"));
      });
      rows.forEach(function (row) { body.appendChild(row); });
      buttons.forEach(function (button) {
        var header = button.parentNode;
        header.setAttribute("aria-sort", button.getAttribute("data-sort") === key ? "descending" : "none");
      });
      var address = key === "pts" ? window.location.pathname : window.location.pathname + "?sort=" + key;
      if (window.location.pathname + window.location.search !== address) {
        window.history.replaceState(null, "", address);
      }
    };

    buttons.forEach(function (button) {
      button.addEventListener("click", function () { sortBy(button.getAttribute("data-sort")); });
    });

    var requested = new URLSearchParams(window.location.search).get("sort");
    if (requested === "gf") {
      sortBy("gf");
    }
  }

  var print = document.getElementById("print-table");
  if (print) {
    print.addEventListener("click", function () {
      var on = document.body.classList.toggle("print-layout");
      print.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  var star = document.getElementById("follow-team");
  if (star) {
    star.addEventListener("click", function () {
      var on = star.getAttribute("aria-pressed") !== "true";
      star.setAttribute("aria-pressed", on ? "true" : "false");
      star.textContent = on ? star.getAttribute("data-on") : star.getAttribute("data-off");
    });
  }
})();
