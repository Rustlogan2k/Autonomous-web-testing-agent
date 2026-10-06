/* Harbourside Health: client-side touches on server-rendered pages.
   The server decides everything that matters; this file only shows and hides parts of a
   page without a reload, and keeps the payment form's hidden `payment_mode` field (the
   value the server books with) in step with the visible "How will you pay?" select. */
(function () {
  "use strict";

  // A page restored from the back/forward cache would show stale form state: fetch it again.
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) window.location.reload();
  });

  // Payment: the insurer section only appears for insurer-paid visits.
  var choice = document.getElementById("payment");
  var payMode = document.getElementById("payment-mode");
  var insurerBlock = document.getElementById("insurer-fields");
  function showPayment() {
    var insurer = choice.value === "insurer";
    insurerBlock.hidden = !insurer;
    if (choice.value) payMode.value = choice.value;
  }
  if (choice && payMode && insurerBlock) {
    choice.addEventListener("change", showPayment);
    showPayment();
  }

  // "Next available": bring the first free time on the table into view.
  var nextFree = document.getElementById("next-free");
  if (nextFree) {
    nextFree.addEventListener("click", function () {
      document.querySelectorAll("td.spotlight").forEach(function (cell) { cell.classList.remove("spotlight"); });
      var first = document.querySelector("button.slot-free");
      if (!first) return;
      first.closest("td").classList.add("spotlight");
      first.scrollIntoView({ block: "center" });
    });
  }

  // "Print summary": a print-friendly layout of the page, nothing more.
  var printView = document.getElementById("print-view");
  if (printView) {
    printView.addEventListener("click", function () {
      var on = document.body.classList.toggle("print-friendly");
      printView.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  // Cancelling needs the confirmation box ticked first.
  var tick = document.getElementById("cancel-confirm");
  var cancelButton = document.getElementById("cancel-visit");
  if (tick && cancelButton) {
    var syncCancel = function () { cancelButton.disabled = !tick.checked; };
    tick.addEventListener("change", syncCancel);
    syncCancel();
  }
})();
