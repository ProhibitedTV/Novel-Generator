document.addEventListener("DOMContentLoaded", () => {
  const modelInputs = Array.from(document.querySelectorAll("[data-model-input]"));
  const modelChoices = Array.from(document.querySelectorAll("[data-model-choice]"));

  function syncChoices(inputId) {
    const input = document.getElementById(inputId);
    if (!input) {
      return;
    }

    const currentValue = input.value.trim();
    modelChoices
      .filter((choice) => choice.dataset.target === inputId)
      .forEach((choice) => {
        const isSelected = choice.dataset.model === currentValue;
        choice.setAttribute("aria-pressed", isSelected ? "true" : "false");
      });
  }

  modelInputs.forEach((input) => {
    syncChoices(input.id);
    input.addEventListener("input", () => syncChoices(input.id));
    input.addEventListener("change", () => syncChoices(input.id));
  });

  modelChoices.forEach((choice) => {
    choice.addEventListener("click", () => {
      const targetId = choice.dataset.target;
      const model = choice.dataset.model || "";
      const input = document.getElementById(targetId);

      if (!input) {
        return;
      }

      input.value = model;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      input.focus();
      input.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  });
});
