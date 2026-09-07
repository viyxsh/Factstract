const API_BASE = "/api";

const state = {
  documents: [],
  facts: [],
  relationships: [],
  failures: [],
  jobs: new Map(),
  selectedTab: "facts",
  selectedDocumentId: null,
  selectedFactId: null,
  selectedRelationshipId: null,
  selectedFailureId: null,
  evidenceFactId: null,
  health: null,
  relationshipFilters: { corroborates: true, contradicts: true, reconciles: true },
};

const els = {
  healthDot: document.getElementById("healthDot"),
  healthText: document.getElementById("healthText"),
  healthDetail: document.getElementById("healthDetail"),
  documentCount: document.getElementById("documentCount"),
  documentList: document.getElementById("documentList"),
  factCount: document.getElementById("factCount"),
  docCount: document.getElementById("docCount"),
  relationshipCount: document.getElementById("relationshipCount"),
  failureCount: document.getElementById("failureCount"),
  factsList: document.getElementById("factsList"),
  relationshipsList: document.getElementById("relationshipsList"),
  evidenceList: document.getElementById("evidenceList"),
  evidenceDrawer: document.getElementById("evidenceDrawer"),
  evidenceTitle: document.getElementById("evidenceTitle"),
  evidenceMeta: document.getElementById("evidenceMeta"),
  evidenceQuote: document.getElementById("evidenceQuote"),
  evidenceStatus: document.getElementById("evidenceStatus"),
  failuresList: document.getElementById("failuresList"),
  jobBanner: document.getElementById("jobBanner"),
  jobTitle: document.getElementById("jobTitle"),
  jobState: document.getElementById("jobState"),
  jobProgress: document.getElementById("jobProgress"),
  jobProgressFill: document.getElementById("jobProgressFill"),
  jobMessage: document.getElementById("jobMessage"),
  uploadTrigger: document.getElementById("uploadTrigger"),
  demoLoadTrigger: document.getElementById("demoLoadTrigger"),
  fileInput: document.getElementById("fileInput"),
  tabs: Array.from(document.querySelectorAll(".tab")),
  panels: Array.from(document.querySelectorAll("[data-panel]")),
  documentSearch: document.getElementById("documentSearch"),
  documentOptions: document.getElementById("documentOptions"),
  documentDropdownToggle: document.getElementById("documentDropdownToggle"),
  factsHeading: document.getElementById("factsHeading"),
  relationshipKindFilters: Array.from(document.querySelectorAll("[data-kind-filter]")),
};

function titleCase(value) {
  return String(value || "")
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function clearNode(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

function escapeText(value) {
  return value == null || value === "" ? "—" : String(value);
}

function formatPage(pageNumber) {
  return pageNumber ? `p.${pageNumber}` : "p. ?";
}

function matchDocument(item) {
  if (!state.selectedDocumentId) {
    return true;
  }
  return item.document_id === state.selectedDocumentId;
}

function filteredFacts() {
  return state.facts.filter(matchDocument);
}

function filteredRelationships() {
  let relationships = state.relationships;
  if (!state.selectedDocumentId) {
    relationships = state.relationships;
  } else {
    const factIds = new Set(filteredFacts().map((fact) => fact.id));
    relationships = relationships.filter(
      (relationship) => factIds.has(relationship.left_fact_id) || factIds.has(relationship.right_fact_id),
    );
  }
  return relationships.filter((relationship) => state.relationshipFilters[relationship.kind] !== false);
}

function filteredFailures() {
  if (!state.selectedDocumentId) {
    return state.failures;
  }
  return state.failures.filter((failure) => failure.document_id === state.selectedDocumentId);
}

function statusClass(kind) {
  if (kind === "corroborates") return "is-good";
  if (kind === "contradicts") return "is-bad";
  return "is-warn";
}

function relationshipLabel(kind) {
  if (kind === "reconciles") return "Reconciled";
  if (!kind) return "Relationship";
  return `${kind.charAt(0).toUpperCase()}${kind.slice(1)}`;
}

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      Accept: "application/json",
      ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch {}
    throw new Error(detail);
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

function showEmpty(node, message) {
  clearNode(node);
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.textContent = message;
  node.appendChild(empty);
}

function renderHealth() {
  if (!state.health) {
    els.healthDot.classList.remove("is-on");
    els.healthText.textContent = "API unavailable";
    els.healthDetail.textContent = "No health response yet.";
    return;
  }
  els.healthDot.classList.toggle("is-on", Boolean(state.health.llm_configured));
  els.healthText.textContent = state.health.ok ? "API online" : "API degraded";
  els.healthDetail.textContent = state.health.llm_configured
    ? "Live LLM configured through OpenRouter."
    : "Demo mode only. Live LLM key not configured.";
}

function renderSummary(summary) {
  els.factCount.textContent = escapeText(summary?.facts ?? state.facts.length);
  els.docCount.textContent = escapeText(summary?.documents ?? state.documents.length);
  els.relationshipCount.textContent = escapeText(summary?.relationships ?? state.relationships.length);
  els.failureCount.textContent = escapeText(state.failures.length);
}

function renderDocuments() {
  clearNode(els.documentList);
  els.documentCount.textContent = `${state.documents.length} docs`;
  if (!state.documents.length) {
    showEmpty(els.documentList, "No documents uploaded yet.");
    return;
  }
  for (const doc of state.documents) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `document-item${state.selectedDocumentId === doc.id ? " is-active" : ""}`;
    button.addEventListener("click", () => {
      state.selectedDocumentId = doc.id;
      state.selectedFactId = null;
      state.selectedRelationshipId = null;
      state.selectedFailureId = null;
      renderAll();
    });

    const title = document.createElement("div");
    title.className = "doc-title";
    title.textContent = doc.filename;

    const meta = document.createElement("div");
    meta.className = "doc-meta";
    meta.append(
      document.createTextNode(`${doc.status || "unknown"} · ${doc.page_count ?? 0} pages`),
      document.createElement("span"),
    );
    meta.lastChild.textContent = doc.sha256 ? doc.sha256.slice(0, 10) : "no hash";

    button.append(title, meta);
    els.documentList.appendChild(button);
  }
}

function renderDocumentFilter() {
  if (!els.documentSearch || !els.documentOptions) {
    return;
  }
  const query = els.documentSearch.value.trim().toLowerCase();
  const documents = state.documents;
  clearNode(els.documentOptions);

  const allOption = document.createElement("li");
  allOption.textContent = "All documents";
  allOption.dataset.value = "";
  allOption.className = state.selectedDocumentId == null ? "is-active" : "";
  if (!query || "all documents".includes(query)) {
    els.documentOptions.appendChild(allOption);
  }

  let visibleCount = 0;
  for (const doc of documents) {
    const matches = !query || doc.filename.toLowerCase().includes(query);
    if (!matches) {
      continue;
    }
    visibleCount += 1;
    const option = document.createElement("li");
    option.textContent = doc.filename;
    option.dataset.value = String(doc.id);
    option.title = doc.filename;
    if (state.selectedDocumentId === doc.id) {
      option.classList.add("is-active");
    }
    els.documentOptions.appendChild(option);
  }

  if (!visibleCount && query) {
    const empty = document.createElement("li");
    empty.className = "is-empty";
    empty.textContent = "No documents match.";
    els.documentOptions.appendChild(empty);
  }

  if (state.selectedDocumentId) {
    const chosen = documents.find((doc) => doc.id === state.selectedDocumentId);
    if (chosen && document.activeElement !== els.documentSearch) {
      els.documentSearch.value = chosen.filename;
    }
  } else if (document.activeElement !== els.documentSearch) {
    els.documentSearch.value = "";
  }
}

function factLabel(fact) {
  const parts = [fact.subject, fact.metric].filter(Boolean);
  return parts.join(" · ") || `Fact ${fact.id}`;
}

function renderFacts() {
  clearNode(els.factsList);
  renderDocumentFilter();
  const facts = filteredFacts();
  els.factsHeading.textContent = state.selectedDocumentId ? "Facts for selected document" : "Grounded facts";

  if (!facts.length) {
    showEmpty(els.factsList, "No grounded facts available for this view.");
    return;
  }

  for (const fact of facts) {
    const card = document.createElement("article");
    card.className = "fact-card";
    if (fact.id === state.selectedFactId) {
      card.style.borderColor = "rgba(90, 167, 255, 0.45)";
    }

    const heading = document.createElement("h4");
    heading.textContent = factLabel(fact);

    const meta = document.createElement("div");
    meta.className = "fact-meta";
    meta.append(
      document.createTextNode(`${fact.grounding_status || "grounded"} · ${formatPage(fact.page_number)} · ${escapeText(fact.period)}`),
    );

    const line = document.createElement("div");
    line.className = "metric-line";
    const value = document.createElement("strong");
    value.textContent = escapeText(fact.value_display);
    const unit = document.createElement("span");
    unit.textContent = escapeText(fact.unit);
    line.append(value, unit);

    const quote = document.createElement("p");
    quote.className = "muted";
    quote.textContent = fact.quote || "No quote available.";

    const actions = document.createElement("div");
    actions.className = "fact-actions";
    const openEvidence = document.createElement("button");
    openEvidence.type = "button";
    openEvidence.className = "ghost-button";
    openEvidence.textContent = "Open evidence";
    openEvidence.addEventListener("click", () => openEvidenceForFact(fact.id));
    actions.appendChild(openEvidence);

    card.addEventListener("click", () => {
      state.selectedFactId = fact.id;
      state.evidenceFactId = fact.id;
      state.selectedRelationshipId = null;
      renderAll();
    });

    card.append(heading, meta, line, quote, actions);
    els.factsList.appendChild(card);
  }
}

function renderRelationships() {
  clearNode(els.relationshipsList);
  const relationships = filteredRelationships();
  if (!relationships.length) {
    showEmpty(els.relationshipsList, "No relationships available for this view.");
    return;
  }

  for (const relationship of relationships) {
    const left = state.facts.find((fact) => fact.id === relationship.left_fact_id);
    const right = state.facts.find((fact) => fact.id === relationship.right_fact_id);
    const card = document.createElement("article");
    card.className = "relationship-card";
    if (relationship.id === state.selectedRelationshipId) {
      card.style.borderColor = "rgba(90, 167, 255, 0.45)";
    }

    const badge = document.createElement("span");
    badge.className = `relationship-kind ${statusClass(relationship.kind)}`;
    badge.textContent = `${relationshipLabel(relationship.kind)} — ${relationship.title || "comparison"}`;

    const grid = document.createElement("div");
    grid.className = "relationship-grid";

    const makeSide = (fact, label) => {
      const side = document.createElement("div");
      side.className = "relationship-side";
      const sideLabel = document.createElement("p");
      sideLabel.className = "card-label";
      sideLabel.textContent = label;
      const sideTitle = document.createElement("h4");
      sideTitle.textContent = fact ? factLabel(fact) : "Missing fact";
      const sideValue = document.createElement("div");
      sideValue.className = "metric-line";
      const main = document.createElement("strong");
      main.textContent = fact ? escapeText(fact.value_display) : "—";
      const unit = document.createElement("span");
      unit.textContent = fact ? `${escapeText(fact.unit)} · ${formatPage(fact.page_number)}` : "—";
      sideValue.append(main, unit);
      const open = document.createElement("button");
      open.type = "button";
      open.className = "ghost-button";
      open.textContent = "View evidence";
      open.disabled = !fact;
      if (fact) {
        open.addEventListener("click", (event) => {
          event.stopPropagation();
          openEvidenceForFact(fact.id);
        });
      }
      side.append(sideLabel, sideTitle, sideValue, open);
      return side;
    };

    grid.append(makeSide(left, "Left fact"), makeSide(right, "Right fact"));

    const rationale = document.createElement("p");
    rationale.className = "muted";
    rationale.textContent = relationship.rationale || "No rationale provided.";

    card.addEventListener("click", () => {
      state.selectedRelationshipId = relationship.id;
      renderAll();
    });

    card.append(badge, grid, rationale);
    els.relationshipsList.appendChild(card);
  }
}

function renderEvidencePanel(fact) {
  if (!fact) {
    els.evidenceTitle.textContent = "Nothing selected";
    els.evidenceMeta.textContent = "";
    els.evidenceQuote.textContent = "Choose a fact from the Evidence tab or use an evidence button on a card.";
    els.evidenceStatus.textContent = "";
    return;
  }
  els.evidenceTitle.textContent = factLabel(fact);
  els.evidenceMeta.textContent = `${escapeText(fact.document_id)} · ${formatPage(fact.page_number)} · ${escapeText(fact.grounding_status)}`;
  els.evidenceQuote.textContent = fact.quote || "No quote available.";
  els.evidenceStatus.textContent = `Value: ${escapeText(fact.value_display)}${fact.unit ? ` ${fact.unit}` : ""}`;
}

function renderEvidenceList() {
  clearNode(els.evidenceList);
  const facts = filteredFacts();
  if (!facts.length) {
    showEmpty(els.evidenceList, "No evidence to show for the selected document.");
    renderEvidencePanel(null);
    return;
  }
  for (const fact of facts) {
    const card = document.createElement("article");
    card.className = "fact-card";
    const heading = document.createElement("h4");
    heading.textContent = factLabel(fact);
    const meta = document.createElement("div");
    meta.className = "fact-meta";
    meta.textContent = `${escapeText(fact.document_id)} · ${formatPage(fact.page_number)} · ${escapeText(fact.confidence)}`;
    const quote = document.createElement("p");
    quote.className = "muted";
    quote.textContent = fact.quote || "No quote available.";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost-button";
    button.textContent = "Load into drawer";
    button.addEventListener("click", () => openEvidenceForFact(fact.id));
    card.append(heading, meta, quote, button);
    els.evidenceList.appendChild(card);
  }
  const active = facts.find((fact) => fact.id === state.evidenceFactId) || facts[0];
  if (active && !state.evidenceFactId) {
    state.evidenceFactId = active.id;
  }
  renderEvidencePanel(active || null);
}

function renderFailures() {
  clearNode(els.failuresList);
  const failures = filteredFailures();
  if (!failures.length) {
    showEmpty(els.failuresList, "No failures have been recorded for this view.");
    return;
  }
  for (const failure of failures) {
    const card = document.createElement("article");
    card.className = "failure-card";
    const heading = document.createElement("h4");
    heading.textContent = `${failure.stage || "failure"} · ${escapeText(failure.reason)}`;
    const meta = document.createElement("div");
    meta.className = "fact-meta";
    meta.textContent = `${escapeText(failure.document_id)} · ${formatPage(failure.page_number)} · ${escapeText(failure.created_at)}`;
    const excerpt = document.createElement("p");
    excerpt.className = "muted";
    excerpt.textContent = failure.source_excerpt || "No excerpt available.";
    const improvement = document.createElement("p");
    improvement.className = "muted";
    improvement.textContent = `Suggested improvement: ${failure.suggested_improvement || "—"}`;
    card.append(heading, meta, excerpt, improvement);
    els.failuresList.appendChild(card);
  }
}

function renderPanels() {
  for (const panel of els.panels) {
    panel.hidden = panel.dataset.panel !== state.selectedTab;
  }
  for (const tab of els.tabs) {
    const active = tab.dataset.tab === state.selectedTab;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
  }
}

function renderJob(job) {
  if (!job) {
    els.jobBanner.hidden = true;
    return;
  }
  els.jobBanner.hidden = false;
  els.jobTitle.textContent = job.document_id ? `Document ${job.document_id}` : "Background job";
  els.jobState.textContent = job.status || "queued";
  els.jobProgress.textContent = `${Math.round((job.progress || 0) * 100)}%`;
  els.jobProgressFill.style.width = `${Math.round((job.progress || 0) * 100)}%`;
  els.jobMessage.textContent = job.message || job.error || "Processing uploaded PDFs.";
}

function renderAll() {
  renderPanels();
  renderHealth();
  renderSummary();
  renderDocuments();
  renderFacts();
  renderRelationships();
  renderEvidenceList();
  renderFailures();
  renderJob(latestJob());
}

function latestJob() {
  const jobs = Array.from(state.jobs.values());
  if (!jobs.length) {
    return null;
  }
  return jobs.sort((left, right) => new Date(right.updated_at || right.created_at || 0) - new Date(left.updated_at || left.created_at || 0))[0];
}

async function refreshAll() {
  const [health, summary, documents, facts, relationships, failures] = await Promise.all([
    request("/health"),
    request("/summary"),
    request("/documents"),
    request("/facts"),
    request("/relationships"),
    request("/failures"),
  ]);
  state.health = health;
  state.documents = Array.isArray(documents) ? documents : [];
  state.facts = Array.isArray(facts) ? facts : [];
  state.relationships = Array.isArray(relationships) ? relationships : [];
  state.failures = Array.isArray(failures) ? failures : [];
  if (!state.selectedDocumentId && state.documents[0]) {
    state.selectedDocumentId = state.documents[0].id;
  }
  if (state.selectedDocumentId && !state.documents.some((doc) => doc.id === state.selectedDocumentId)) {
    state.selectedDocumentId = state.documents[0]?.id || null;
  }
  if (!state.evidenceFactId && state.facts[0]) {
    state.evidenceFactId = state.facts[0].id;
  }
  renderSummary(summary);
  renderAll();
}

async function openEvidenceForFact(factId) {
  state.evidenceFactId = factId;
  const fact = state.facts.find((item) => item.id === factId);
  if (!fact) {
    renderEvidencePanel(null);
    return;
  }
  renderEvidencePanel(fact);
  if (state.selectedTab !== "evidence") {
    state.selectedTab = "evidence";
    renderPanels();
  }
}

async function pollJob(jobId) {
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  while (true) {
    const job = await request(`/jobs/${encodeURIComponent(jobId)}`);
    state.jobs.set(job.id, job);
    renderJob(job);
    if (job.status === "done" || job.status === "failed") {
      break;
    }
    await delay(1200);
  }
}

async function handleUpload() {
  const files = Array.from(els.fileInput.files || []);
  if (!files.length) {
    return;
  }
  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }
  const jobs = await request("/documents", { method: "POST", body: form });
  for (const job of jobs || []) {
    state.jobs.set(job.id, job);
  }
  renderJob(latestJob());
  await Promise.all((jobs || []).map((job) => pollJob(job.id)));
  await refreshAll();
  els.fileInput.value = "";
}

async function loadDemo() {
  const payload = await request("/demo/load", { method: "POST", body: JSON.stringify({}) });
  if (Array.isArray(payload?.jobs)) {
    for (const job of payload.jobs) {
      state.jobs.set(job.id, job);
    }
  }
  await refreshAll();
}

function bindEvents() {
  els.tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      state.selectedTab = tab.dataset.tab;
      renderPanels();
      renderAll();
    });
  });

  els.uploadTrigger.addEventListener("click", () => els.fileInput.click());
  els.fileInput.addEventListener("change", () => {
    handleUpload().catch((error) => {
      els.jobBanner.hidden = false;
      els.jobTitle.textContent = "Upload failed";
      els.jobState.textContent = "failed";
      els.jobMessage.textContent = error.message;
      els.jobProgress.textContent = "0%";
      els.jobProgressFill.style.width = "0%";
    });
  });
  els.demoLoadTrigger.addEventListener("click", () => {
    loadDemo().catch((error) => {
      els.jobBanner.hidden = false;
      els.jobTitle.textContent = "Demo load failed";
      els.jobState.textContent = "failed";
      els.jobMessage.textContent = error.message;
      els.jobProgress.textContent = "0%";
      els.jobProgressFill.style.width = "0%";
    });
  });

  if (els.documentSearch && els.documentOptions) {
    const closeOptions = () => {
      els.documentOptions.hidden = true;
      if (els.documentDropdownToggle) {
        els.documentDropdownToggle.setAttribute("aria-expanded", "false");
      }
    };

    const openOptions = () => {
      els.documentOptions.hidden = false;
      if (els.documentDropdownToggle) {
        els.documentDropdownToggle.setAttribute("aria-expanded", "true");
      }
      renderDocumentFilter();
    };

    els.documentSearch.addEventListener("focus", openOptions);
    els.documentSearch.addEventListener("input", () => {
      renderDocumentFilter();
      if (els.documentOptions.hidden) {
        openOptions();
      }
    });
    els.documentSearch.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeOptions();
      } else if (event.key === "Enter") {
        event.preventDefault();
        const first = els.documentOptions.querySelector("li:not(.is-empty)");
        if (first) {
          first.click();
        }
      }
    });

    if (els.documentDropdownToggle) {
      els.documentDropdownToggle.addEventListener("click", (event) => {
        event.stopPropagation();
        if (els.documentOptions.hidden) {
          openOptions();
          els.documentSearch.focus();
        } else {
          closeOptions();
        }
      });
    }

    els.documentOptions.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement) || !target.dataset || target.classList.contains("is-empty")) {
        return;
      }
      const value = target.dataset.value;
      state.selectedDocumentId = value ? Number(value) : null;
      state.selectedFactId = null;
      state.selectedRelationshipId = null;
      state.selectedFailureId = null;
      els.documentSearch.value = value ? target.textContent || "" : "";
      closeOptions();
      renderAll();
    });

    document.addEventListener("click", (event) => {
      if (!els.documentOptions.hidden) {
        const target = event.target;
        if (target instanceof Node && !els.documentOptions.parentElement?.contains(target)) {
          closeOptions();
        }
      }
    });
  }

  for (const chip of els.relationshipKindFilters) {
    chip.addEventListener("click", () => {
      const kind = chip.dataset.kindFilter;
      const isActive = chip.getAttribute("aria-pressed") !== "false";
      chip.setAttribute("aria-pressed", String(!isActive));
      state.relationshipFilters[kind] = !isActive;
      renderRelationships();
    });
  }
}

bindEvents();
refreshAll().catch((error) => {
  state.health = { ok: false, llm_configured: false };
  els.healthText.textContent = "API error";
  els.healthDetail.textContent = error.message;
  renderAll();
});
