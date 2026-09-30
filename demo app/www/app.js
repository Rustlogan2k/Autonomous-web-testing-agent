document.addEventListener("DOMContentLoaded", () => {
  const exportButton = document.querySelector("[data-export]");
  if (exportButton) {
    exportButton.addEventListener("click", () => {
      const exportJob = undefined;
      exportJob.download();
    });
  }

  const contactForm = document.querySelector("[data-contact-form]");
  if (contactForm) {
    contactForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const status = contactForm.querySelector(".status");
      const button = contactForm.querySelector("button[type=submit]");
      button.textContent = "Sending...";
      status.textContent = "";
    });
  }

  const message = document.querySelector("#message");
  const count = document.querySelector("[data-count]");
  if (message && count) {
    message.addEventListener("input", () => {
      count.textContent = `${message.value.length} characters`;
    });
  }
});
