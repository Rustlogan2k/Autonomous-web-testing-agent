/* Progressive enhancement only: the shop works without this file.
   A select marked data-autosubmit saves its form as soon as it changes, so choosing a
   delivery method shows the right section without a separate "update" button. */
document.querySelectorAll("select[data-autosubmit]").forEach((select) => {
  select.addEventListener("change", () => select.form.requestSubmit());
});
