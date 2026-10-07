// A sighting's page: "Share" reveals the record's address so it can be copied by hand, and
// "Show on map" explains that maps need a connection. Neither talks to the server.
(() => {
  "use strict";

  function reveal(buttonId, panelId, fill) {
    const button = document.getElementById(buttonId);
    const panel = document.getElementById(panelId);
    if (!button || !panel) return;
    button.addEventListener("click", () => {
      if (fill) fill();
      panel.hidden = false;
      button.setAttribute("aria-expanded", "true");
    });
  }

  reveal("share-sighting", "share-panel", () => {
    document.getElementById("share-address").textContent = window.location.origin + window.location.pathname;
  });
  reveal("show-map", "map-panel");
})();
