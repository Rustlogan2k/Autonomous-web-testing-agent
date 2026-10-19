/* Bagstreet's client-side behaviour. Every form also works without it; this adds:
   - a select marked data-autosubmit saves its form as soon as it changes, so choosing a
     delivery method shows the right section without a separate "update" button;
   - "Use a saved address" fills the name and address fields, only when one is chosen;
   - tabs on the product page switch panels in place;
   - confirmation dialogs (deleting an address or a review): a button with data-opens shows
     its dialog and hides the page behind it; Cancel (data-closes) or Escape puts it back. */
(() => {
  "use strict";

  document.querySelectorAll("select[data-autosubmit]").forEach((select) => {
    select.addEventListener("change", () => select.form.requestSubmit());
  });

  const picker = document.querySelector("select[data-fills-address]");
  if (picker) {
    picker.addEventListener("change", () => {
      const chosen = picker.selectedOptions[0];
      if (!chosen || !chosen.value) return; // "—" leaves whatever is typed alone
      document.getElementById("full-name").value = chosen.dataset.name;
      document.getElementById("address").value = chosen.dataset.address;
    });
  }

  const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
  const show = (tab) => {
    tabs.forEach((other) => {
      const selected = other === tab;
      other.setAttribute("aria-selected", selected ? "true" : "false");
      document.getElementById(other.getAttribute("aria-controls")).hidden = !selected;
    });
  };
  tabs.forEach((tab) => tab.addEventListener("click", () => show(tab)));

  const page = document.getElementById("page");
  let open = null;
  let opener = null;
  const close = () => {
    if (!open) return;
    open.hidden = true;
    page.hidden = false;
    open = null;
    if (opener) opener.focus();
  };
  document.querySelectorAll("[data-opens]").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = document.getElementById(button.dataset.opens);
      if (!dialog) return;
      close();
      opener = button;
      open = dialog;
      page.hidden = true; // the page behind a modal dialog cannot be used
      dialog.hidden = false;
      const first = dialog.querySelector("button");
      if (first) first.focus();
    });
  });
  document.querySelectorAll("[data-closes]").forEach((button) => button.addEventListener("click", close));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
  });
})();
