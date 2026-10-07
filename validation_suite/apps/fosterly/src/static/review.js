// The review page's "Print a copy": switches the page to its print layout (no navigation,
// no request) and says how to print it. Pressing it again switches back.
(() => {
  "use strict";

  const button = document.getElementById("print-copy");
  const note = document.getElementById("print-note");
  if (!button || !note) return;
  button.addEventListener("click", () => {
    const on = !document.body.classList.contains("print-layout");
    document.body.classList.toggle("print-layout", on);
    note.hidden = !on;
    button.setAttribute("aria-pressed", String(on));
  });
})();
