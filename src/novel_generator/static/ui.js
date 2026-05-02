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
  const eventLogCountNode = document.querySelector("[data-event-log-count]");
  const statusNode = runDetail.querySelector("[data-run-status-label]");
  const stepNode = runDetail.querySelector("[data-run-step-label]");
  const chapterNode = runDetail.querySelector("[data-run-chapter-label]");
  const routeNode = runDetail.querySelector("[data-run-route-label]");
  const elapsedNode = runDetail.querySelector("[data-run-elapsed]");
  const completedChapterNode = runDetail.querySelector("[data-run-completed-chapters]");
  const wordProgressNode = runDetail.querySelector("[data-run-word-progress]");
  const wordProgressLabelNode = runDetail.querySelector("[data-run-word-progress-label]");
  const artifactCountNode = runDetail.querySelector("[data-run-artifact-count]");
  const eventCountNode = runDetail.querySelector("[data-run-event-count]");
  const stepper = runDetail.querySelector("[data-run-stepper]");
  const stageTitleNode = document.querySelector("[data-stage-title]");
  const stageDescriptionNode = document.querySelector("[data-stage-description]");
  const stageWhyNode = document.querySelector("[data-stage-why]");
  const stageResultNode = document.querySelector("[data-stage-result]");
  const stageNextNode = document.querySelector("[data-stage-next-label]");
  const contractNode = document.querySelector("[data-current-contract]");
  const contractEmptyNode = document.querySelector("[data-current-contract-empty]");
  const contractChapterNode = document.querySelector("[data-contract-chapter]");
  const contractTitleNode = document.querySelector("[data-contract-title]");
  const contractStatusNode = document.querySelector("[data-contract-status]");
  const contractObjectiveNode = document.querySelector("[data-contract-objective]");
  const contractObstacleNode = document.querySelector("[data-contract-obstacle]");
  const contractCharacterTurnNode = document.querySelector("[data-contract-character-turn]");
  const contractCostNode = document.querySelector("[data-contract-cost]");
  const contractFrictionNode = document.querySelector("[data-contract-friction]");
  const contractModeNode = document.querySelector("[data-contract-mode]");
  const contractEmotionalNode = document.querySelector("[data-contract-emotional-reveal]");
  const contractCivilianNode = document.querySelector("[data-contract-civilian]");
  const contractIdeologyNode = document.querySelector("[data-contract-ideology]");
  const contractAttemptNode = document.querySelector("[data-contract-attempt]");
  const contractPriceNode = document.querySelector("[data-contract-price]");
  const contractEndingNode = document.querySelector("[data-contract-ending]");
  const qualitySourceNode = document.querySelector("[data-quality-source-label]");
  const qualitySignalsNode = document.querySelector("[data-quality-signals]");
  const qualityEmptyNode = document.querySelector("[data-quality-empty]");
  const revisionTriggersNode = document.querySelector("[data-revision-triggers]");
  const revisionEmptyNode = document.querySelector("[data-revision-empty]");
  const continuitySourceNode = document.querySelector("[data-continuity-source-label]");
  const continuityPanelNode = document.querySelector("[data-continuity-panel]");
  const continuityEmptyNode = document.querySelector("[data-continuity-empty]");
  const continuityOutcomeNode = document.querySelector("[data-continuity-outcome]");
  const continuityPatchNode = document.querySelector("[data-continuity-patch]");
  const continuityWorldNode = document.querySelector("[data-continuity-world]");
  const continuityTimelineNode = document.querySelector("[data-continuity-timeline]");
  const continuityEntitiesNode = document.querySelector("[data-continuity-entities]");
  const continuityPromisesNode = document.querySelector("[data-continuity-promises]");
  const continuityEmotionsNode = document.querySelector("[data-continuity-emotions]");
  const continuityCiviliansNode = document.querySelector("[data-continuity-civilians]");
  const stageJsonNode = document.querySelector("[data-run-stages-json]");
  const stageData = stageJsonNode ? JSON.parse(stageJsonNode.textContent || "[]") : [];
  const stageLookup = Object.fromEntries(stageData.map((stage) => [stage.id, stage]));
  const stageOrder = stageData.filter((stage) => stage.id !== "failed" && stage.id !== "canceled").map((stage) => stage.id);
  const qualityDefs = [
    { field: "forward_motion_score", label: "Forward motion", lowerIsBetter: false },
    { field: "ending_concreteness_score", label: "Ending concreteness", lowerIsBetter: false },
    { field: "cost_consequence_realism_score", label: "Cost realism", lowerIsBetter: false },
    { field: "emotional_depth_score", label: "Emotional depth", lowerIsBetter: false },
    { field: "side_character_independence_score", label: "Side-character agency", lowerIsBetter: false },
    { field: "proper_noun_continuity_score", label: "Proper-noun continuity", lowerIsBetter: false },
    { field: "ideology_clarity_score", label: "Ideology clarity", lowerIsBetter: false },
    { field: "civilian_texture_score", label: "Civilian texture", lowerIsBetter: false },
    { field: "repetition_risk_score", label: "Repetition risk", lowerIsBetter: true },
  ];
  const terminalStatuses = new Set(["completed", "failed", "canceled"]);
  const reviewStatuses = new Set(["awaiting_approval"]);
  let latestRunData = null;
  let refreshTimer = null;
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

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll("\"", "&quot;")
      .replaceAll("'", "&#39;");
  }

  function setHidden(node, hidden) {
    if (!node) {
      return;
    }
    node.classList.toggle("is-hidden", hidden);
  }

  function stageFor(stageId) {
    return stageLookup[stageId] || {
      id: stageId,
      label: String(stageId || "queued").replace(/_/g, " "),
      description: "The run is between known pipeline stages.",
      why: "This stage does not have a richer explanation yet.",
      result: "Check the event feed for the most recent worker action.",
    };
  }

  function scoreState(score, lowerIsBetter) {
    if (lowerIsBetter) {
      if (score <= 3) {
        return { tone: "healthy", label: "Low risk" };
      }
      if (score <= 5) {
        return { tone: "watch", label: "Watch" };
      }
      return { tone: "risk", label: "High risk" };
    }
    if (score >= 8) {
      return { tone: "healthy", label: "Strong" };
    }
    if (score >= 6) {
      return { tone: "steady", label: "Healthy" };
    }
    if (score >= 4) {
      return { tone: "watch", label: "Watch" };
    }
    return { tone: "risk", label: "Weak" };
  }

  function parseDate(value) {
    if (!value) {
      return null;
    }
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  function formatDuration(ms) {
    const totalSeconds = Math.max(0, Math.floor(ms / 1000));
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;

    if (hours > 0) {
      return `${hours}h ${String(minutes).padStart(2, "0")}m ${String(seconds).padStart(2, "0")}s`;
    }
    if (minutes > 0) {
      return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
    }
    return `${seconds}s`;
  }

  function sortedEvents(run) {
    return Array.from(run?.events || []).sort((left, right) => (left.sequence || 0) - (right.sequence || 0));
  }

  function sortedChapters(run) {
    return Array.from(run?.chapters || []).sort((left, right) => (left.chapter_number || 0) - (right.chapter_number || 0));
  }

  function totalWords(run) {
    return sortedChapters(run).reduce((sum, chapter) => sum + Number(chapter.word_count || 0), 0);
  }

  function completedChapterCount(run) {
    return sortedChapters(run).filter((chapter) => chapter.content && chapter.summary && chapter.continuity_update).length;
  }

  function currentDashboardChapter(run) {
    const chapters = sortedChapters(run);
    if (!chapters.length) {
      return null;
    }
    if (run.current_chapter != null) {
      const current = chapters.find((chapter) => Number(chapter.chapter_number) === Number(run.current_chapter));
      if (current) {
        return current;
      }
    }
    const incomplete = chapters.find((chapter) => chapter.status !== "completed");
    return incomplete || chapters[chapters.length - 1];
  }

  function latestQualityChapter(run, preferredChapter) {
    if (preferredChapter?.qa_notes) {
      return preferredChapter;
    }
    const chapters = sortedChapters(run).reverse();
    return chapters.find((chapter) => chapter.qa_notes) || preferredChapter || null;
  }

  function latestContinuityChapter(run, preferredChapter) {
    if (preferredChapter?.continuity_update) {
      return preferredChapter;
    }
    const chapters = sortedChapters(run).reverse();
    return chapters.find((chapter) => chapter.continuity_update) || preferredChapter || null;
  }

  function outlineEntry(run, chapterNumber) {
    return Array.from(run?.outline || []).find((entry) => Number(entry.chapter_number) === Number(chapterNumber)) || null;
  }

  function parsePlan(planText) {
    if (!planText) {
      return {};
    }
    try {
      const parsed = JSON.parse(planText);
      return typeof parsed === "object" && parsed !== null ? parsed : {};
    } catch {
      return {};
    }
  }

  function currentContract(run) {
    const chapter = currentDashboardChapter(run);
    if (!chapter) {
      return null;
    }
    const outline = outlineEntry(run, chapter.chapter_number) || {};
    const endingHook = outline.concrete_ending_hook || {};
    const plan = parsePlan(chapter.plan);
    return {
      chapter,
      chapterNumber: chapter.chapter_number,
      title: chapter.title,
      status: chapter.status || "pending",
      objective: outline.objective || chapter.outline_summary || "",
      primaryObstacle: outline.primary_obstacle || "",
      characterTurn: outline.character_turn || "",
      costIfSuccess: outline.cost_if_success || "",
      sideCharacterFriction: outline.side_character_friction || "",
      chapterMode: outline.chapter_mode || "",
      civilianLifeDetail: outline.civilian_life_detail || "",
      emotionalReveal: outline.emotional_reveal || plan.emotional_anchor || "",
      ideologyPressure: outline.ideology_pressure || plan.ideology_clash || "",
      attempt: plan.attempt || "",
      complication: plan.complication || "",
      pricePaid: plan.price_paid || plan.partial_failure_mode || "",
      endingHook: [endingHook.trigger || "-", endingHook.visible_object_or_actor || "-", endingHook.next_problem || plan.ending_hook_delivery || "-"].join(" / "),
    };
  }

  function activeRoute(run) {
    const events = sortedEvents(run).reverse();
    for (const event of events) {
      const providerName = String(event.payload?.provider_name || "").trim();
      const modelName = String(event.payload?.model_name || "").trim();
      if (providerName || modelName) {
        return {
          providerName: providerName || run.provider_name,
          modelName: modelName || run.model_name,
        };
      }
    }
    return {
      providerName: run.provider_name,
      modelName: run.model_name,
    };
  }

  function buildQualitySignals(chapter) {
    if (!chapter?.qa_notes) {
      return [];
    }
    return qualityDefs.map((definition) => {
      const score = Number(chapter.qa_notes?.[definition.field] || 0);
      const state = scoreState(score, definition.lowerIsBetter);
      return {
        label: definition.label,
        score,
        tone: state.tone,
        stateLabel: state.label,
      };
    });
  }

  function buildRevisionTriggers(chapter) {
    if (!chapter?.qa_notes) {
      return [];
    }
    const rows = [];
    const repairScope = String(chapter.qa_notes?.repair_scope || "none").trim();
    if (repairScope && repairScope !== "none") {
      rows.push({ tone: "info", text: `Repair scope used: ${repairScope.replaceAll("_", " ")}.` });
    }
    for (const item of chapter.qa_notes?.blocking_issues || []) {
      rows.push({ tone: "error", text: String(item) });
    }
    for (const item of chapter.qa_notes?.soft_warnings || []) {
      rows.push({ tone: "warning", text: String(item) });
    }
    for (const item of chapter.qa_notes?.focus || []) {
      rows.push({ tone: "neutral", text: String(item) });
    }

    const deduped = [];
    const seen = new Set();
    for (const row of rows) {
      if (!row.text || seen.has(row.text)) {
        continue;
      }
      seen.add(row.text);
      deduped.push(row);
    }
    return deduped.slice(0, 8);
  }

  function continuitySnapshot(run, preferredChapter) {
    const chapter = latestContinuityChapter(run, preferredChapter);
    const ledger = run.continuity_ledger || {};
    if (!chapter && !Object.keys(ledger).length) {
      return null;
    }
    const update = chapter?.continuity_update || {};
    const mapList = (value) =>
      Object.entries(value || {}).map(([key, entry]) => `${key}: ${entry}`);

    return {
      chapterNumber: chapter?.chapter_number || null,
      chapterOutcome: update.chapter_outcome || "",
      currentPatchStatus: update.current_patch_status || ledger.current_patch_status || "",
      worldState: update.world_state || ledger.world_state || "",
      timelineEntry: update.timeline_entry || "",
      entityChanges: mapList(update.entity_state_changes),
      openPromises: mapList(update.open_promises_by_name),
      emotionalLoops: mapList(update.emotional_open_loops),
      civilianPressure: Array.from(update.civilian_pressure_points || []),
    };
  }

  function renderList(node, items, fallback) {
    if (!node) {
      return;
    }
    if (!items.length) {
      node.innerHTML = `<li>${escapeHtml(fallback)}</li>`;
      return;
    }
    node.innerHTML = items.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  }

  function renderStagePanel(run) {
    const normalizedStage = normalizeStage(run.current_step || "", run.status || "");
    const currentStage = stageFor(normalizedStage);
    const nextStageIndex = stageOrder.indexOf(normalizedStage);
    const nextStage = nextStageIndex >= 0 && nextStageIndex + 1 < stageOrder.length ? stageFor(stageOrder[nextStageIndex + 1]) : null;

    if (stageTitleNode) {
      stageTitleNode.textContent = currentStage.label;
    }
    if (stageDescriptionNode) {
      stageDescriptionNode.textContent = currentStage.description;
    }
    if (stageWhyNode) {
      stageWhyNode.textContent = currentStage.why;
    }
    if (stageResultNode) {
      stageResultNode.textContent = currentStage.result;
    }
    if (stageNextNode) {
      if (nextStage) {
        stageNextNode.textContent = `Next: ${nextStage.label}`;
        setHidden(stageNextNode, false);
      } else {
        setHidden(stageNextNode, true);
      }
    }
  }

  function renderContract(run) {
    const contract = currentContract(run);
    setHidden(contractNode, !contract);
    setHidden(contractEmptyNode, Boolean(contract));
    if (!contract) {
      return;
    }

    if (contractChapterNode) {
      contractChapterNode.textContent = String(contract.chapterNumber);
    }
    if (contractTitleNode) {
      contractTitleNode.textContent = contract.title || "Waiting for chapter planning";
    }
    if (contractStatusNode) {
      contractStatusNode.textContent = String(contract.status || "pending").replace(/_/g, " ");
      contractStatusNode.className = `badge status-${contract.status || "pending"}`;
    }
    if (contractObjectiveNode) {
      contractObjectiveNode.textContent = contract.objective || "-";
    }
    if (contractObstacleNode) {
      contractObstacleNode.textContent = contract.primaryObstacle || "-";
    }
    if (contractCharacterTurnNode) {
      contractCharacterTurnNode.textContent = contract.characterTurn || "-";
    }
    if (contractCostNode) {
      contractCostNode.textContent = contract.costIfSuccess || "-";
    }
    if (contractFrictionNode) {
      contractFrictionNode.textContent = contract.sideCharacterFriction || "-";
    }
    if (contractModeNode) {
      contractModeNode.textContent = contract.chapterMode || "-";
    }
    if (contractEmotionalNode) {
      contractEmotionalNode.textContent = contract.emotionalReveal || "-";
    }
    if (contractCivilianNode) {
      contractCivilianNode.textContent = contract.civilianLifeDetail || "-";
    }
    if (contractIdeologyNode) {
      contractIdeologyNode.textContent = contract.ideologyPressure || "-";
    }
    if (contractAttemptNode) {
      contractAttemptNode.textContent = `${contract.attempt || "-"} / ${contract.complication || "-"}`;
    }
    if (contractPriceNode) {
      contractPriceNode.textContent = contract.pricePaid || "-";
    }
    if (contractEndingNode) {
      contractEndingNode.textContent = contract.endingHook;
    }
  }

  function renderQuality(run) {
    const preferredChapter = currentDashboardChapter(run);
    const sourceChapter = latestQualityChapter(run, preferredChapter);
    const signals = buildQualitySignals(sourceChapter);
    const triggers = buildRevisionTriggers(sourceChapter);

    if (qualitySourceNode) {
      qualitySourceNode.textContent = sourceChapter?.qa_notes ? `Latest chapter: ${sourceChapter.chapter_number}` : "Waiting for the first critique";
    }
    setHidden(qualitySignalsNode, !signals.length);
    setHidden(qualityEmptyNode, Boolean(signals.length));
    if (qualitySignalsNode) {
      qualitySignalsNode.innerHTML = signals
        .map(
          (signal) => `
            <article class="signal-card signal-${escapeHtml(signal.tone)}">
              <div class="signal-topline">
                <strong>${escapeHtml(signal.label)}</strong>
                <span>${escapeHtml(signal.score)}/10</span>
              </div>
              <div class="signal-meter" aria-hidden="true">
                <span style="width: ${Math.max(0, Math.min(100, signal.score * 10))}%"></span>
              </div>
              <p class="field-note">${escapeHtml(signal.stateLabel)}</p>
            </article>
          `
        )
        .join("");
    }

    setHidden(revisionTriggersNode, !triggers.length);
    setHidden(revisionEmptyNode, Boolean(triggers.length));
    if (revisionTriggersNode) {
      revisionTriggersNode.innerHTML = triggers.map((item) => `<li class="alert-${escapeHtml(item.tone)}">${escapeHtml(item.text)}</li>`).join("");
    }
  }

  function renderContinuity(run) {
    const snapshot = continuitySnapshot(run, currentDashboardChapter(run));
    if (continuitySourceNode) {
      continuitySourceNode.textContent = snapshot?.chapterNumber ? `Latest checkpoint: Chapter ${snapshot.chapterNumber}` : "Waiting for the first continuity update";
    }
    setHidden(continuityPanelNode, !snapshot);
    setHidden(continuityEmptyNode, Boolean(snapshot));
    if (!snapshot) {
      return;
    }
    if (continuityOutcomeNode) {
      continuityOutcomeNode.textContent = snapshot.chapterOutcome || "-";
    }
    if (continuityPatchNode) {
      continuityPatchNode.textContent = snapshot.currentPatchStatus || "-";
    }
    if (continuityWorldNode) {
      continuityWorldNode.textContent = snapshot.worldState || "-";
    }
    if (continuityTimelineNode) {
      continuityTimelineNode.textContent = snapshot.timelineEntry || "-";
    }
    renderList(continuityEntitiesNode, snapshot.entityChanges, "No named-entity changes recorded yet.");
    renderList(continuityPromisesNode, snapshot.openPromises, "No open promises recorded yet.");
    renderList(continuityEmotionsNode, snapshot.emotionalLoops, "No emotional fallout recorded yet.");
    renderList(continuityCiviliansNode, snapshot.civilianPressure, "No civilian pressure points recorded yet.");
  }

  function updateElapsed() {
    if (!elapsedNode) {
      return;
    }
    const startedAt = parseDate(runDetail.dataset.runStartedAt || "");
    const createdAt = parseDate(runDetail.dataset.runCreatedAt || "");
    const completedAt = parseDate(runDetail.dataset.runCompletedAt || "");
    const status = runDetail.dataset.runStatus || "";
    const now = new Date();

    if (startedAt) {
      const end = completedAt || now;
      elapsedNode.textContent = formatDuration(end.getTime() - startedAt.getTime());
      return;
    }
    if (terminalStatuses.has(status)) {
      elapsedNode.textContent = "Not started";
      return;
    }
    if (createdAt) {
      elapsedNode.textContent = `Queued for ${formatDuration(now.getTime() - createdAt.getTime())}`;
      return;
    }
    elapsedNode.textContent = "Starting soon";
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
      stepNode.textContent = stageFor(normalizedStage).label;
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

  function updateDashboardCounts(run) {
    const completedChapters = completedChapterCount(run);
    const words = totalWords(run);
    const targetWords = Number(run.target_word_count || 0);
    const progressPercent = targetWords > 0 ? Math.min(100, Math.round((words / targetWords) * 100)) : 0;

    if (completedChapterNode) {
      completedChapterNode.textContent = String(completedChapters);
    }
    if (wordProgressNode) {
      wordProgressNode.textContent = String(words);
    }
    if (wordProgressLabelNode) {
      wordProgressLabelNode.textContent = `${progressPercent}%`;
    }
    if (artifactCountNode) {
      artifactCountNode.textContent = String(Array.from(run.artifacts || []).length);
    }
    if (eventCountNode) {
      eventCountNode.textContent = String(sortedEvents(run).length);
    }
  }

  function renderRunDashboard(run) {
    latestRunData = run;
    runDetail.dataset.runStatus = run.status || runDetail.dataset.runStatus || "";
    runDetail.dataset.runStep = run.current_step || "";
    runDetail.dataset.runCurrentChapter = run.current_chapter || "";
    runDetail.dataset.runStartedAt = run.started_at || runDetail.dataset.runStartedAt || "";
    runDetail.dataset.runCompletedAt = run.completed_at || "";

    updateStatus(run.status || "queued", run.current_step || "queued", run.current_chapter || "");
    renderStagePanel(run);
    renderContract(run);
    renderQuality(run);
    renderContinuity(run);
    updateDashboardCounts(run);
    updateElapsed();

    const route = activeRoute(run);
    if (routeNode) {
      routeNode.textContent = `${route.providerName} / ${route.modelName}`;
    }
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
    const routeNote = document.createElement("p");
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
    routeNote.className = "field-note";
    if (payload.payload?.provider_name || payload.payload?.model_name) {
      routeNote.textContent = [payload.payload?.provider_name, payload.payload?.model_name].filter(Boolean).join(" / ");
    }

    topline.appendChild(kind);
    topline.appendChild(sequence);
    item.appendChild(topline);
    item.appendChild(copy);
    if (routeNote.textContent) {
      item.appendChild(routeNote);
    }
    eventLog.appendChild(item);
    eventLog.scrollTop = eventLog.scrollHeight;
    if (eventLogCountNode) {
      eventLogCountNode.textContent = String(eventLog.querySelectorAll("[data-sequence]").length);
    }
  }

  async function refreshRun() {
    if (!runId) {
      return;
    }
    try {
      const run = await fetchJson(`/api/runs/${runId}`);
      renderRunDashboard(run);
    } catch {
      // Keep the current page state if the refresh fails; the SSE feed still has the latest event.
    }
  }

  function scheduleRefresh(immediate = false) {
    if (refreshTimer) {
      window.clearTimeout(refreshTimer);
    }
    refreshTimer = window.setTimeout(() => {
      refreshTimer = null;
      refreshRun();
    }, immediate ? 0 : 150);
  }

  updateStatus(runDetail.dataset.runStatus || "queued", runDetail.dataset.runStep || "queued", runDetail.dataset.runCurrentChapter || "");
  updateElapsed();
  window.setInterval(updateElapsed, 1000);
  scheduleRefresh(true);

  if (!runId || terminalStatuses.has(runDetail.dataset.runStatus || "") || reviewStatuses.has(runDetail.dataset.runStatus || "")) {
    return;
  }

  const source = new EventSource(`/api/runs/${runId}/events`);

  source.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    appendEvent(payload);
    updateStatus(payload.run_status || "queued", payload.current_step || "queued", payload.current_chapter || "");
    scheduleRefresh();
    if (reviewStatuses.has(payload.run_status || "")) {
      source.close();
      window.setTimeout(() => window.location.reload(), 800);
    }
  };

  source.addEventListener("terminal", (event) => {
    const payload = JSON.parse(event.data);
    updateStatus(payload.run_status || "queued", payload.current_step || "queued", payload.current_chapter || "");
    scheduleRefresh(true);
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
