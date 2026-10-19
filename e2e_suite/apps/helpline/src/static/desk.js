/* Helpline's fragment updates: a small, dependency-free take on "HTML over the wire".
 *
 * - <form data-fragment> is submitted with fetch instead of navigating. A GET form whose
 *   data-fragment holds a URL is sent there with its fields as the query string.
 * - <a data-fragment="/url"> and <button data-fragment="/url"> fetch that URL
 *   (data-keep-query appends the page's current query string).
 * - The response is HTML: every top-level element with an id replaces the element on the
 *   page with the same id. An X-Desk-Url header becomes the page's address
 *   (history.replaceState), applied after the swap so address and content always agree.
 *   An X-Desk-Signed-Out header sends the browser to the sign-in page.
 * - [data-opens=id] shows a dialog (resetting its form), [data-dismiss=id] hides it,
 *   [data-hide=id] hides an element for this page view only, [data-toggle=id] shows or hides
 *   a menu, and [data-insert=select] copies the chosen saved reply into [data-into=textarea].
 * - A saved reply's title is renamed in place (Rename, type, Save) without leaving the list.
 *
 * One click sends at most one request, and the page is updated as soon as it answers.
 * Requests carry X-Desk-Fragment: 1; without this script the forms post normally and the
 * server answers with redirects and whole pages.
 */
(function () {
  "use strict";

  function currentTab() {
    return new URLSearchParams(window.location.search).get("tab") || "conversation";
  }

  function syncModal() {
    const open = document.querySelector('[role="dialog"]:not([hidden])');
    document.body.classList.toggle("modal-open", Boolean(open));
    if (open) {
      const choice = open.querySelector("select[required]");
      const confirm = open.querySelector('button[type="submit"]');
      if (choice && confirm) confirm.disabled = !choice.value;
    }
  }

  function swap(markup) {
    const holder = document.createElement("template");
    holder.innerHTML = markup;
    Array.from(holder.content.children).forEach(function (incoming) {
      const existing = incoming.id && document.getElementById(incoming.id);
      if (existing) existing.replaceWith(incoming);
    });
  }

  function exchange(url, init) {
    const headers = { "X-Desk-Fragment": "1" };
    if (init && init.body) headers["Content-Type"] = "application/x-www-form-urlencoded";
    return fetch(url, Object.assign({ credentials: "same-origin", headers: headers }, init || {}))
      .then(function (response) {
        if (response.headers.get("X-Desk-Signed-Out")) {
          window.location.assign("/login");
          return undefined;
        }
        return response.text().then(function (markup) {
          swap(markup);
          const address = response.headers.get("X-Desk-Url");
          if (address) window.history.replaceState(null, "", address);
          syncModal();
        });
      })
      .catch(function () {
        /* The page was left while the request was in flight: nothing to update. */
      });
  }

  function openDialog(id) {
    const dialog = document.getElementById(id);
    if (!dialog) return;
    const form = dialog.querySelector("form");
    if (form) form.reset();
    const confirm = dialog.querySelector('button[type="submit"]');
    const choice = dialog.querySelector("select[required]");
    if (confirm && choice) confirm.disabled = !choice.value;
    const flash = document.getElementById("ticket-flash");
    if (flash) flash.textContent = "";
    dialog.hidden = false;
    syncModal();
    if (choice) choice.focus();
  }

  function closeDialog(id) {
    const dialog = document.getElementById(id);
    if (dialog) dialog.hidden = true;
    syncModal();
  }

  function renameReply(item) {
    const id = item.getAttribute("data-title-of");
    const shown = document.getElementById("title-" + id);
    const editor = document.getElementById("title-editor-" + id);
    const input = document.getElementById("title-input-" + id);
    const status = document.getElementById("title-status-" + id);
    const button = document.getElementById("edit-title-" + id);
    function done() { editor.hidden = true; button.hidden = false; }
    button.addEventListener("click", function () {
      input.value = item.getAttribute("data-saved");
      editor.hidden = false;
      button.hidden = true;
      status.textContent = "";
    });
    document.getElementById("cancel-title-" + id).addEventListener("click", done);
    document.getElementById("save-title-" + id).addEventListener("click", function () {
      fetch("/replies/" + encodeURIComponent(id) + "/title", {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: input.value })
      }).then(function (response) { return response.json(); }).then(function (reply) {
        if (reply.reason === "signed-out") { window.location.assign("/login"); return; }
        if (!reply.ok) { status.textContent = reply.error || ""; return; }
        shown.textContent = reply.title;
        item.setAttribute("data-saved", reply.title);
        done();
      }).catch(function () { status.textContent = ""; });
    });
  }

  document.addEventListener("submit", function (event) {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-fragment")) return;
    event.preventDefault();
    const fields = new URLSearchParams(new FormData(form));
    if ((form.getAttribute("method") || "get").toLowerCase() === "get") {
      const target = form.getAttribute("data-fragment") || form.getAttribute("action");
      exchange(target + "?" + fields.toString());
      return;
    }
    fields.set("_tab", currentTab());
    exchange(form.getAttribute("action"), { method: "POST", body: fields.toString() });
  });

  document.addEventListener("click", function (event) {
    const trigger = event.target.closest("a[data-fragment], button[data-fragment]");
    if (trigger) {
      event.preventDefault();
      let url = trigger.getAttribute("data-fragment");
      if (trigger.hasAttribute("data-keep-query")) url += window.location.search;
      exchange(url);
      return;
    }
    const opener = event.target.closest("[data-opens]");
    if (opener) {
      openDialog(opener.getAttribute("data-opens"));
      return;
    }
    const dismiss = event.target.closest("[data-dismiss]");
    if (dismiss) {
      closeDialog(dismiss.getAttribute("data-dismiss"));
      return;
    }
    const toggle = event.target.closest("[data-toggle]");
    if (toggle) {
      const menu = document.getElementById(toggle.getAttribute("data-toggle"));
      if (menu) {
        menu.hidden = !menu.hidden;
        toggle.setAttribute("aria-expanded", menu.hidden ? "false" : "true");
      }
      return;
    }
    const insert = event.target.closest("[data-insert]");
    if (insert) {
      const picker = document.getElementById(insert.getAttribute("data-insert"));
      const target = document.getElementById(insert.getAttribute("data-into"));
      const chosen = picker && picker.selectedOptions[0];
      if (target && chosen && chosen.value) target.value = chosen.getAttribute("data-body");
      return;
    }
    const hider = event.target.closest("[data-hide]");
    if (hider) {
      const target = document.getElementById(hider.getAttribute("data-hide"));
      if (target) target.hidden = true;
    }
  });

  document.addEventListener("change", function (event) {
    const choice = event.target;
    if (!(choice instanceof HTMLSelectElement) || !choice.required) return;
    const dialog = choice.closest('[role="dialog"]');
    const confirm = dialog && dialog.querySelector('button[type="submit"]');
    if (confirm) confirm.disabled = !choice.value;
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    const open = document.querySelector('[role="dialog"]:not([hidden])');
    if (open) closeDialog(open.id);
  });

  document.querySelectorAll("[data-title-of]").forEach(renameReply);
  syncModal();
})();
