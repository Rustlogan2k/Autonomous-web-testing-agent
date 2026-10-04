/* Helpline's fragment updates: a small, dependency-free take on "HTML over the wire".
 *
 * - <form data-fragment> is submitted with fetch instead of navigating. A GET form whose
 *   data-fragment holds a URL is sent there with its fields as the query string.
 * - <a data-fragment="/url"> and <button data-fragment="/url"> fetch that URL
 *   (data-keep-query appends the page's current query string).
 * - The response is HTML: every top-level element with an id replaces the element on the
 *   page with the same id. An X-Desk-Url header becomes the page's address
 *   (history.replaceState), applied after the swap so address and content always agree.
 * - [data-opens=id] shows a dialog (resetting its form), [data-dismiss=id] hides it, and
 *   [data-hide=id] hides an element for this page view only.
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

  syncModal();
})();
