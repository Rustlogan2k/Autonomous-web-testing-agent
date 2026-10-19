/* Harbourside Health: the front desk's in-page behaviour.
   - the account menu in the header (Sign out is one click behind it);
   - confirmation dialogs: a button with data-dialog opens that <dialog>, data-close shuts it;
   - waiting-list notes, edited in place: the new text is shown at once and saved to the
     server; if the server refuses it, the old text comes back. */
(function () {
  "use strict";

  var accountToggle = document.getElementById("nav-account");
  var accountMenu = document.getElementById("account-menu");
  if (accountToggle && accountMenu) {
    accountToggle.addEventListener("click", function () {
      accountMenu.hidden = !accountMenu.hidden;
      accountToggle.setAttribute("aria-expanded", accountMenu.hidden ? "false" : "true");
    });
  }

  document.querySelectorAll("button[data-dialog]").forEach(function (opener) {
    var dialog = document.getElementById(opener.dataset.dialog);
    if (!dialog || typeof dialog.showModal !== "function") return;
    opener.addEventListener("click", function () { dialog.showModal(); });
    dialog.querySelectorAll("[data-close]").forEach(function (closer) {
      closer.addEventListener("click", function () { dialog.close(); });
    });
  });

  document.querySelectorAll("tr[data-note-entry]").forEach(function (row) {
    var id = row.dataset.noteEntry;
    var shown = document.getElementById("note-" + id);
    var editor = document.getElementById("note-editor-" + id);
    var input = document.getElementById("note-input-" + id);
    var edit = document.getElementById("edit-note-" + id);
    var status = document.getElementById("note-status-" + id);
    function close() { editor.hidden = true; edit.hidden = false; }
    edit.addEventListener("click", function () {
      input.value = row.dataset.saved;
      editor.hidden = false;
      edit.hidden = true;
      status.textContent = "";
      input.focus();
    });
    document.getElementById("cancel-note-" + id).addEventListener("click", close);
    document.getElementById("save-note-" + id).addEventListener("click", function () {
      var text = input.value.trim();
      var before = row.dataset.saved;
      shown.textContent = text;
      close();
      if (text === before) return;  // unchanged: nothing to send
      fetch("/waiting/" + encodeURIComponent(id) + "/note", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note: text })
      }).then(function (response) { return response.json(); }).then(function (reply) {
        if (reply.ok) {
          row.dataset.saved = reply.note;
          status.textContent = "";
          return;
        }
        if (reply.reason === "signed-out") { window.location.assign("/login"); return; }
        shown.textContent = before;  // refused: put the saved text back
        status.textContent = reply.error || "";
      }).catch(function () {
        shown.textContent = before;
        status.textContent = "";
      });
    });
  });
})();
