window.addEventListener("DOMContentLoaded", () => {
  document.body.classList.add("govuk-template__body", "js-enabled", "govuk-frontend-supported");
  if (window.GOVUKFrontend && typeof window.GOVUKFrontend.initAll === "function") {
    window.GOVUKFrontend.initAll();
  }
});
