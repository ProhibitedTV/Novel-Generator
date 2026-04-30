document.addEventListener("DOMContentLoaded", () => {
  setupConfirmActions(document);
  setupModelPickers(document);
  setupProviderConsole();
  setupRunDetail();
});

function setupConfirmActions(root) {
  const confirmForms = Array.from(root.querySelectorAll("form[data-confirm]"));
  confirmForms.forEach((form) => {
    if (form.dataset.confirmBound === "true") {
      return;
    }
    form.dataset.confirmBound = "true";
    form.addEventListener("submit", (event) => {
      const message = form.dataset.confirm || "Are you sure you want to continue?";
      if (!window.confirm(message)) {
        event.preventDefault();
      }
    });
  });
}

function setupModelPickers(root) {
  const modelInputs = Array.from(root.querySelectorAll("[data-model-input]"));
  const modelChoices = Array.from(root.querySelectorAll("[data-model-choice]"));

  function syncChoices(inputId) {
    const input = document.getElementById(inputId);
    if (!input) {
      return;
    }

    const currentValue = input.value.trim();
    Array.from(document.querySelectorAll(`[data-model-choice][data-target="${inputId}"]`)).forEach((choice) => {
      const isSelected = choice.dataset.model === currentValue;
      choice.setAttribute("aria-pressed", isSelected ? "true" : "false");
    });
  }

  modelInputs.forEach((input) => {
    syncChoices(input.id);
    input.addEventListener("input", () => syncChoices(input.id));
    input.addEventListener("change", () => syncChoices(input.id));
  });

  modelChoices.forEach((choice) => bindModelChoice(choice, syncChoices));
}

function bindModelChoice(choice, syncChoices) {
  if (choice.dataset.bound === "true") {
    return;
  }
  choice.dataset.bound = "true";
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
    syncChoices(targetId);
  });
}

function createModelChip(targetId, modelName, syncChoices) {
  const item = document.createElement("li");
  const button = document.createElement("button");
  const label = document.createElement("span");

  button.type = "button";
  button.className = "model-chip";
  button.dataset.modelChoice = "";
  button.dataset.target = targetId;
  button.dataset.model = modelName;
  button.setAttribute("aria-pressed", "false");

  label.className = "model-chip-value";
  label.textContent = modelName;

  button.appendChild(label);
  item.appendChild(button);
  bindModelChoice(button, syncChoices);
  return item;
}

async function fetchJson(url) {
  const response = await fetch(url, {
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}.`);
  }

  return response.json();
}

function setupProviderConsole() {
  const consoleNode = document.querySelector("[data-provider-console]");
  if (!consoleNode) {
    return;
  }

  const badge = consoleNode.querySelector("[data-provider-badge]");
  const baseUrlNode = consoleNode.querySelector("[data-provider-base-url]");
  const defaultModelNode = consoleNode.querySelector("[data-provider-default-model]");
  const modelCountNode = consoleNode.querySelector("[data-provider-model-count]");
  const errorNode = consoleNode.querySelector("[data-provider-error]");
  const modelListNode = document.querySelector("[data-provider-model-list]");
  const modelEmptyNode = document.querySelector("[data-provider-model-empty]");
  const statusButton = consoleNode.querySelector('[data-provider-action="status"]');
  const modelsButton = consoleNode.querySelector('[data-provider-action="models"]');
  const targetId = modelListNode?.dataset.modelTarget || "";

  function setProviderFeedback(message, tone = "neutral") {
    if (!errorNode) {
      return;
    }

    errorNode.textContent = message;
    if (tone === "error") {
      errorNode.className = "error-text";
      return;
    }
    if (tone === "success") {
      errorNode.className = "inline-note inline-note-success";
      return;
    }
    errorNode.className = "field-note";
  }

  function syncChoices(inputId) {
    const input = document.getElementById(inputId);
    if (!input) {
      return;
    }
    const currentValue = input.value.trim();
    Array.from(document.querySelectorAll(`[data-model-choice][data-target="${inputId}"]`)).forEach((choice) => {
      choice.setAttribute("aria-pressed", choice.dataset.model === currentValue ? "true" : "false");
    });
  }

  function renderStatus(providerStatus) {
    if (badge) {
      badge.textContent = providerStatus.reachable ? "Connected" : "Unavailable";
      badge.className = `badge status-${providerStatus.reachable ? "running" : "failed"}`;
    }
    if (baseUrlNode) {
      baseUrlNode.textContent = providerStatus.base_url;
    }
    if (defaultModelNode) {
      defaultModelNode.textContent = providerStatus.default_model;
    }
    if (modelCountNode) {
      modelCountNode.textContent = String((providerStatus.available_models || []).length);
    }
    if (errorNode) {
      if (providerStatus.error) {
        setProviderFeedback(providerStatus.error, "error");
      } else {
        setProviderFeedback("", "neutral");
      }
    }
  }

  function renderModels(models) {
    if (!modelListNode) {
      return;
    }

    modelListNode.innerHTML = "";
    if (!models.length) {
      modelListNode.classList.add("is-hidden");
      if (modelEmptyNode) {
        modelEmptyNode.classList.remove("is-hidden");
        modelEmptyNode.textContent = "No models are currently detected from the saved provider connection.";
      }
      if (modelCountNode) {
        modelCountNode.textContent = "0";
      }
      return;
    }

    models.forEach((modelName) => {
      modelListNode.appendChild(createModelChip(targetId, modelName, syncChoices));
    });
    modelListNode.classList.remove("is-hidden");
    if (modelEmptyNode) {
      modelEmptyNode.classList.add("is-hidden");
    }
    if (modelCountNode) {
      modelCountNode.textContent = String(models.length);
    }
    if (targetId) {
      syncChoices(targetId);
    }
  }

  async function refreshStatus(button) {
    const previousText = button ? button.textContent : "";
    if (button) {
      button.disabled = true;
      button.textContent = "Testing...";
    }

    try {
      const statusPayload = await fetchJson("/api/providers/ollama/status");
      renderStatus(statusPayload);
      renderModels(statusPayload.available_models || []);
      if (statusPayload.reachable) {
        setProviderFeedback("Saved provider connection responded successfully.", "success");
      }
    } catch (error) {
      setProviderFeedback(error.message, "error");
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = previousText;
      }
    }
  }

  async function refreshModels(button) {
    const previousText = button ? button.textContent : "";
    if (button) {
      button.disabled = true;
      button.textContent = "Refreshing...";
    }

    try {
      const modelsPayload = await fetchJson("/api/providers/ollama/models");
      renderModels(modelsPayload.models || []);
      const statusPayload = await fetchJson("/api/providers/ollama/status");
      renderStatus(statusPayload);
      if (statusPayload.reachable) {
        setProviderFeedback(`Refreshed ${modelsPayload.models?.length || 0} detected model(s) from the saved provider.`, "success");
      }
    } catch (error) {
      setProviderFeedback(error.message, "error");
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = previousText;
      }
    }
  }

  if (statusButton) {
    statusButton.addEventListener("click", () => refreshStatus(statusButton));
  }
  if (modelsButton) {
    modelsButton.addEventListener("click", () => refreshModels(modelsButton));
  }
}

function setupRunDetail() {
  const runDetail = document.querySelector("[data-run-detail]");
  if (!runDetail) {
    return;
  }

  const runId = runDetail.dataset.runId;
  const eventLog = document.querySelector("[data-event-log]");
  const statusNode = runDetail.querySelector("[data-run-status-label]");
  const stepNode = runDetail.querySelector("[data-run-step-label]");
  const chapterNode = runDetail.querySelector("[data-run-chapter-label]");
  const stepper = runDetail.querySelector("[data-run-stepper]");
  const stageOrder = [
    "queued",
    "story_bible",
    "outline",
    "outline_review",
    "chapter_plan",
    "chapter_draft",
    "chapter_revision",
    "chapter_summary",
    "manuscript_qa",
    "export",
    "completed",
  ];
  const terminalStatuses = new Set(["completed", "failed", "canceled"]);
  const reviewStatuses = new Set(["awaiting_approval"]);
  let lastNonTerminalStep = normalizeStage(runDetail.dataset.runStep || "", runDetail.dataset.runStatus || "");

  function normalizeStage(step, status) {
    if (status === "awaiting_approval") {
      return "outline_review";
    }
    if (status === "completed" || status === "failed" || status === "canceled") {
      return status;
    }
    if (step === "starting" || step === "recovered" || step === "" || step == null) {
      return "queued";
    }
    return step;
  }

  function updateStatus(status, step, currentChapter) {
    const normalizedStage = normalizeStage(step, status);
    if (!terminalStatuses.has(normalizedStage)) {
      lastNonTerminalStep = normalizedStage;
    }

    runDetail.dataset.runStatus = status;
    runDetail.dataset.runStep = step || "";
    runDetail.dataset.runCurrentChapter = currentChapter || "";

    if (statusNode) {
      statusNode.textContent = String(status).replace(/_/g, " ");
      statusNode.className = `badge status-${status}`;
    }
    if (stepNode) {
      stepNode.textContent = step ? String(step).replace(/_/g, " ") : "-";
    }
    if (chapterNode) {
      chapterNode.textContent = currentChapter || "-";
    }

    if (!stepper) {
      return;
    }

    Array.from(stepper.querySelectorAll("[data-stage]")).forEach((stageNode) => {
      stageNode.classList.remove("is-complete", "is-current", "is-terminal");
      const stageId = stageNode.dataset.stage || "";

      if (status === "completed") {
        if (stageOrder.includes(stageId)) {
          stageNode.classList.add("is-complete");
        }
        if (stageId === "completed") {
          stageNode.classList.add("is-current");
        }
        return;
      }

      if (status === "failed" || status === "canceled") {
        const lastIndex = stageOrder.indexOf(lastNonTerminalStep);
        const stageIndex = stageOrder.indexOf(stageId);
        if (stageIndex >= 0 && stageIndex <= lastIndex) {
          stageNode.classList.add("is-complete");
        }
        if (stageId === status) {
          stageNode.classList.add("is-current", "is-terminal");
        }
        return;
      }

      if (status === "awaiting_approval") {
        const currentIndex = stageOrder.indexOf("outline_review");
        const stageIndex = stageOrder.indexOf(stageId);
        if (stageIndex >= 0 && stageIndex < currentIndex) {
          stageNode.classList.add("is-complete");
        }
        if (stageId === "outline_review") {
          stageNode.classList.add("is-current");
        }
        return;
      }

      const currentIndex = stageOrder.indexOf(normalizedStage);
      const stageIndex = stageOrder.indexOf(stageId);
      if (stageIndex >= 0 && currentIndex >= 0 && stageIndex < currentIndex) {
        stageNode.classList.add("is-complete");
      }
      if (stageId === normalizedStage) {
        stageNode.classList.add("is-current");
      }
    });
  }

  function appendEvent(payload) {
    if (!eventLog) {
      return;
    }

    const item = document.createElement("li");
    const topline = document.createElement("div");
    const kind = document.createElement("span");
    const sequence = document.createElement("span");
    const copy = document.createElement("p");
    const summary = payload.payload?.message || payload.payload?.title || payload.payload?.chapter_number || "Waiting for the next update.";

    item.className = "event-item";
    item.dataset.sequence = payload.sequence || "";

    topline.className = "event-topline";
    kind.className = "event-kind";
    kind.textContent = String(payload.event_type || "update").replace(/_/g, " ");
    sequence.className = "event-sequence";
    sequence.textContent = `#${payload.sequence || "-"}`;
    copy.className = "event-copy";
    copy.textContent = String(summary);

    topline.appendChild(kind);
    topline.appendChild(sequence);
    item.appendChild(topline);
    item.appendChild(copy);
    eventLog.appendChild(item);
    eventLog.scrollTop = eventLog.scrollHeight;
  }

  updateStatus(runDetail.dataset.runStatus || "queued", runDetail.dataset.runStep || "queued", runDetail.dataset.runCurrentChapter || "");

  if (!runId || terminalStatuses.has(runDetail.dataset.runStatus || "") || reviewStatuses.has(runDetail.dataset.runStatus || "")) {
    return;
  }

  const source = new EventSource(`/api/runs/${runId}/events`);

  source.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    appendEvent(payload);
    updateStatus(payload.run_status || "queued", payload.current_step || "queued", payload.current_chapter || "");
    if (reviewStatuses.has(payload.run_status || "")) {
      source.close();
      window.setTimeout(() => window.location.reload(), 800);
    }
  };

  source.addEventListener("terminal", (event) => {
    const payload = JSON.parse(event.data);
    updateStatus(payload.run_status || "queued", payload.current_step || "queued", payload.current_chapter || "");
    source.close();
    if (terminalStatuses.has(payload.run_status)) {
      window.setTimeout(() => window.location.reload(), 1000);
    }
  });

  source.onerror = () => {
    if (terminalStatuses.has(runDetail.dataset.runStatus || "")) {
      source.close();
    }
  };
}
