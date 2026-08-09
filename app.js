const promptInput = document.querySelector("#promptInput");
const runNameInput = document.querySelector("#runNameInput");
const modelSelect = document.querySelector("#modelSelect");
const reasoningSelect = document.querySelector("#reasoningSelect");
const universeSelect = document.querySelector("#universeSelect");
const companyCountInput = document.querySelector("#companyCountInput");
const maxTokensInput = document.querySelector("#maxTokensInput");
const companyCountHelp = document.querySelector("#companyCountHelp");
const listEditorSelect = document.querySelector("#listEditorSelect");
const listNameInput = document.querySelector("#listNameInput");
const stockSearchInput = document.querySelector("#stockSearchInput");
const stockSearchResults = document.querySelector("#stockSearchResults");
const selectedStocks = document.querySelector("#selectedStocks");
const selectedStockCount = document.querySelector("#selectedStockCount");
const newListButton = document.querySelector("#newListButton");
const saveListButton = document.querySelector("#saveListButton");
const archiveListButton = document.querySelector("#archiveListButton");
const listStatus = document.querySelector("#listStatus");
const runButton = document.querySelector("#runButton");
const statusEl = document.querySelector("#status");
const runsStatusEl = document.querySelector("#runsStatus");
const runRows = document.querySelector("#runRows");
const homeTabs = [...document.querySelectorAll("[data-home-tab]")];
const homePanels = [...document.querySelectorAll("[data-home-panel]")];
const pageParams = new URLSearchParams(window.location.search);
const editRunId = pageParams.get("editRun");
let companiesAvailable = 0;
let allCompanies = [];
let stockLists = [];
let editingListId = null;
let selectedTickers = [];
let topCompanyCount = Number(companyCountInput.value) || 10;
let runSnapshotUniverse = null;

function resizePromptInput() {
  promptInput.style.height = "auto";
  const borderHeight = promptInput.offsetHeight - promptInput.clientHeight;
  promptInput.style.height = `${promptInput.scrollHeight + borderHeight}px`;
}

function activeTabFromHash() {
  const requested = window.location.hash.replace(/^#/, "");
  return homeTabs.some((tab) => tab.dataset.homeTab === requested) ? requested : "set-run";
}

function selectHomeTab(tabName, updateHash = true) {
  const selectedName = homeTabs.some((tab) => tab.dataset.homeTab === tabName)
    ? tabName
    : "set-run";
  for (const tab of homeTabs) {
    const selected = tab.dataset.homeTab === selectedName;
    tab.classList.toggle("is-active", selected);
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  }
  for (const panel of homePanels) {
    panel.hidden = panel.dataset.homePanel !== selectedName;
  }
  if (updateHash && window.location.hash !== `#${selectedName}`) {
    window.location.hash = selectedName;
  }
}

function setRunRows(message) {
  runRows.innerHTML = `<tr><td colspan="7">${escapeHtml(message)}</td></tr>`;
}

async function fetchJson(url) {
  let lastError = null;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const response = await fetch(url, { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `Could not load ${url}`);
      return payload;
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function companyByTicker(ticker) {
  return allCompanies.find((company) => company.ticker === ticker);
}

function companyPickerRow(company, action, label) {
  const logo = company.logo
    ? `<img src="${escapeHtml(company.logo)}" alt="" loading="lazy" onerror="this.hidden=true" />`
    : "<span></span>";
  return `
    <div class="${action === "add" ? "stock-picker-row" : "selected-stock-row"}">
      ${logo}
      <div class="stock-picker-copy">
        <strong>${escapeHtml(company.name)}</strong>
        <span>${escapeHtml(company.ticker)} - Market cap rank ${escapeHtml(company.rank)}</span>
      </div>
      <button
        class="${action === "remove" ? "secondary-button " : ""}stock-picker-action"
        type="button"
        data-${action}-ticker="${escapeHtml(company.ticker)}"
        aria-label="${escapeHtml(label)} ${escapeHtml(company.name)}"
      >${action === "add" ? "Add" : "Remove"}</button>
    </div>
  `;
}

function renderStockPicker() {
  const query = stockSearchInput.value.trim().toLowerCase();
  const selected = new Set(selectedTickers);
  const matches = allCompanies
    .filter((company) => !selected.has(company.ticker))
    .filter(
      (company) =>
        !query ||
        company.name.toLowerCase().includes(query) ||
        company.ticker.toLowerCase().includes(query)
    )
    .slice(0, 12);

  stockSearchResults.innerHTML = matches.length
    ? matches.map((company) => companyPickerRow(company, "add", "Add")).join("")
    : '<p class="stock-picker-empty">No matching stocks available.</p>';

  const selectedCompanies = selectedTickers.map(companyByTicker).filter(Boolean);
  selectedStockCount.textContent = String(selectedCompanies.length);
  selectedStocks.innerHTML = selectedCompanies.length
    ? selectedCompanies.map((company) => companyPickerRow(company, "remove", "Remove")).join("")
    : '<p class="stock-picker-empty">Add stocks from the search results.</p>';
}

function resetListEditor() {
  editingListId = null;
  selectedTickers = [];
  listEditorSelect.value = "";
  listNameInput.value = "";
  stockSearchInput.value = "";
  archiveListButton.disabled = true;
  listStatus.textContent = "Creating a new list.";
  renderStockPicker();
  listNameInput.focus();
}

function openListEditor(listId) {
  const stockList = stockLists.find((item) => String(item.id) === String(listId));
  if (!stockList) {
    resetListEditor();
    return;
  }
  editingListId = stockList.id;
  selectedTickers = stockList.companies.map((company) => company.ticker);
  listEditorSelect.value = String(stockList.id);
  listNameInput.value = stockList.name;
  stockSearchInput.value = "";
  archiveListButton.disabled = false;
  listStatus.textContent = `${stockList.company_count} stocks saved.`;
  renderStockPicker();
}

function renderStockListSelectors(preferredUniverseValue = universeSelect.value) {
  universeSelect.innerHTML = '<option value="top">Top companies by market cap</option>';
  listEditorSelect.innerHTML = '<option value="">New list</option>';
  for (const stockList of stockLists) {
    const universeOption = document.createElement("option");
    universeOption.value = `list:${stockList.id}`;
    universeOption.textContent = `${stockList.name} (${stockList.company_count})`;
    universeSelect.append(universeOption);

    const editorOption = document.createElement("option");
    editorOption.value = String(stockList.id);
    editorOption.textContent = `${stockList.name} (${stockList.company_count})`;
    listEditorSelect.append(editorOption);
  }
  if (runSnapshotUniverse) {
    const snapshotOption = document.createElement("option");
    snapshotOption.value = `snapshot:${runSnapshotUniverse.run_id}`;
    snapshotOption.textContent = `${runSnapshotUniverse.name} (${runSnapshotUniverse.company_count})`;
    universeSelect.append(snapshotOption);
  }
  universeSelect.value = [...universeSelect.options].some((option) => option.value === preferredUniverseValue)
    ? preferredUniverseValue
    : "top";
  listEditorSelect.value = editingListId === null ? "" : String(editingListId);
  updateUniverseControls();
}

function selectedUniverse() {
  if (universeSelect.value.startsWith("list:")) {
    const listId = Number(universeSelect.value.slice(5));
    const stockList = stockLists.find((item) => item.id === listId);
    return stockList ? { ...stockList, kind: "list" } : null;
  }
  if (universeSelect.value.startsWith("snapshot:") && runSnapshotUniverse) {
    return runSnapshotUniverse;
  }
  return null;
}

function updateUniverseControls() {
  const stockList = selectedUniverse();
  companyCountInput.disabled = false;
  if (stockList) {
    companyCountInput.max = String(stockList.company_count);
    companyCountInput.value = String(stockList.company_count);
    companyCountHelp.textContent = `Choose 1-${stockList.company_count}. Scoring starts from the first stocks saved in "${stockList.name}".`;
  } else {
    companyCountInput.max = String(companiesAvailable);
    companyCountInput.value = String(topCompanyCount);
    companyCountHelp.textContent = `Choose 1-${companiesAvailable}. Scoring starts from the largest companies by market cap.`;
  }
}

function formatDate(timestamp) {
  if (!timestamp) return "";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(timestamp * 1000));
}

function progress(run) {
  return `${run.completed_count + run.failed_count}/${run.company_count}`;
}

function statusClass(status) {
  if (status === "completed") return "pill good";
  if (status === "failed") return "pill bad";
  if (status === "running") return "pill live";
  return "pill";
}

function formatCost(value) {
  const cents = Number(value || 0) * 100;
  if (!cents) return "0.0000 cents";
  return `${cents.toLocaleString(undefined, {
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  })} cents`;
}

async function confirmCostEstimate({ model, reasoningMode, companyCount, actionLabel }) {
  const query = new URLSearchParams({
    model,
    reasoningMode: reasoningMode || "none",
    companyCount: String(companyCount),
  });
  const payload = await fetchJson(`/api/cost-estimate?${query.toString()}`);
  const estimate = payload.estimate;
  const sampleText = estimate.sample_size
    ? `Based on ${estimate.sample_size} recent requests.`
    : "No recent cost history; using a fallback estimate.";
  return window.confirm(
    `${actionLabel}\n\n` +
      `Stocks: ${estimate.company_count}\n` +
      `Estimated cost: ${formatCost(estimate.estimated_cost)}\n` +
      `Average per stock: ${formatCost(estimate.average_request_cost)}\n\n` +
      `${sampleText}\n\nContinue?`
  );
}

async function renameRun(runId, currentName) {
  const name = window.prompt("Rename this run", currentName || "");
  if (name === null) return;
  const trimmed = name.trim();
  if (!trimmed) {
    runsStatusEl.textContent = "Run name is required.";
    return;
  }

  try {
    const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: trimmed }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not rename run");
    runsStatusEl.textContent = `Renamed to ${payload.run.name}.`;
    await loadRuns();
  } catch (error) {
    runsStatusEl.textContent = error.message;
  }
}

async function deleteRun(runId, currentName) {
  const label = currentName || `Run #${runId}`;
  const scrollY = window.scrollY;
  runsStatusEl.textContent = `Archiving ${label}...`;

  try {
    const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`, {
      method: "DELETE",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not archive run");
    runsStatusEl.textContent = `Archived ${label}.`;
    await loadRuns();
    requestAnimationFrame(() => window.scrollTo({ top: scrollY, left: 0 }));
  } catch (error) {
    runsStatusEl.textContent = error.message;
    requestAnimationFrame(() => window.scrollTo({ top: scrollY, left: 0 }));
  }
}

async function loadCompanies() {
  const payload = await fetchJson("/api/companies");
  allCompanies = payload.companies;
  companiesAvailable = allCompanies.length;
  companyCountInput.max = String(companiesAvailable);
  companyCountHelp.textContent = `Choose 1-${companiesAvailable}. Scoring starts from the largest companies by market cap.`;
  renderStockPicker();
}

async function loadModels() {
  const payload = await fetchJson("/api/models");
  modelSelect.innerHTML = "";
  reasoningSelect.innerHTML = "";
  for (const model of payload.models) {
    const option = document.createElement("option");
    option.value = model.id;
    option.textContent = model.label;
    modelSelect.append(option);
  }
  for (const mode of payload.reasoning_modes || []) {
    const option = document.createElement("option");
    option.value = mode.id;
    option.textContent = mode.label;
    reasoningSelect.append(option);
  }
  modelSelect.value = payload.default;
  reasoningSelect.value = payload.default_reasoning_mode || "none";
  maxTokensInput.value = String(payload.default_max_tokens || 200);
}

async function loadStockLists(preferredUniverseValue = universeSelect.value) {
  const payload = await fetchJson("/api/stock-lists");
  stockLists = payload.lists || [];
  renderStockListSelectors(preferredUniverseValue);
}

async function prefillFromRun(runId) {
  if (!runId) return;
  const payload = await fetchJson(`/api/runs/${encodeURIComponent(runId)}`);
  const run = payload.run;

  runNameInput.value = run.name || `Run #${run.id}`;
  promptInput.value = run.prompt || "";
  resizePromptInput();
  topCompanyCount = Number(run.company_count) || topCompanyCount;
  companyCountInput.value = String(topCompanyCount);
  maxTokensInput.value = String(run.max_tokens || 200);

  if (![...modelSelect.options].some((option) => option.value === run.model)) {
    const option = document.createElement("option");
    option.value = run.model;
    option.textContent = run.model_details?.label || run.model;
    modelSelect.append(option);
  }
  modelSelect.value = run.model;
  reasoningSelect.value = run.reasoning_mode || "none";

  if (run.stock_list_id) {
    runSnapshotUniverse = {
      kind: "snapshot",
      run_id: run.id,
      name: `${run.stock_list_name || run.name || `Run #${run.id}`} snapshot`,
      company_count: run.company_count,
      tickers: run.company_tickers || [],
      stock_list_id: run.stock_list_id,
    };
    renderStockListSelectors(`snapshot:${run.id}`);
  } else {
    runSnapshotUniverse = null;
    renderStockListSelectors("top");
  }
  statusEl.textContent = `Loaded settings from ${run.name || `Run #${run.id}`}.`;
}

async function saveCurrentStockList() {
  const name = listNameInput.value.trim();
  if (!name) {
    listStatus.textContent = "Give this list a name first.";
    listNameInput.focus();
    return;
  }
  if (!selectedTickers.length) {
    listStatus.textContent = "Add at least one stock before saving.";
    stockSearchInput.focus();
    return;
  }

  saveListButton.disabled = true;
  try {
    const isUpdate = editingListId !== null;
    const response = await fetch(isUpdate ? `/api/stock-lists/${editingListId}` : "/api/stock-lists", {
      method: isUpdate ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, tickers: selectedTickers }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not save stock list");
    editingListId = payload.list.id;
    const universeValue = `list:${payload.list.id}`;
    await loadStockLists(universeValue);
    openListEditor(payload.list.id);
    listStatus.textContent = `Saved ${payload.list.company_count} stocks in ${payload.list.name}.`;
  } catch (error) {
    listStatus.textContent = error.message;
  } finally {
    saveListButton.disabled = false;
  }
}

async function archiveCurrentStockList() {
  if (editingListId === null) return;
  const archivedId = editingListId;
  archiveListButton.disabled = true;
  listStatus.textContent = "Archiving list...";
  try {
    const response = await fetch(`/api/stock-lists/${archivedId}`, { method: "DELETE" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not archive stock list");
    editingListId = null;
    selectedTickers = [];
    await loadStockLists("top");
    resetListEditor();
    listStatus.textContent = "List archived. Existing runs keep their saved stock snapshot.";
  } catch (error) {
    archiveListButton.disabled = false;
    listStatus.textContent = error.message;
  }
}

async function loadRuns() {
  setRunRows("Loading saved scoring runs...");
  const payload = await fetchJson("/api/runs");

  if (!payload.runs.length) {
    setRunRows("No saved scoring runs yet.");
    return;
  }

  runRows.innerHTML = payload.runs
    .map(
      (run) => `
        <tr class="clickable-row" data-run-id="${run.id}">
          <td>#${run.id}</td>
          <td><strong>${escapeHtml(run.name || `Run #${run.id}`)}</strong></td>
          <td><span class="${statusClass(run.status)}">${escapeHtml(run.status)}</span></td>
          <td>${progress(run)}</td>
          <td>${formatCost(run.cost)}</td>
          <td>${formatDate(run.created_at)}</td>
          <td class="row-actions">
            <div class="row-actions-inner">
              <button
                class="link-button"
                type="button"
                data-rename-run="${run.id}"
                data-run-name="${escapeHtml(run.name || `Run #${run.id}`)}"
              >Rename</button>
              <button
                class="link-button danger-link"
                type="button"
                data-delete-run="${run.id}"
                data-run-name="${escapeHtml(run.name || `Run #${run.id}`)}"
              >Archive</button>
            </div>
          </td>
        </tr>
      `
    )
    .join("");
}

async function createRun() {
  const name = runNameInput.value.trim();
  const prompt = promptInput.value.trim();
  if (!name) {
    statusEl.textContent = "Give this run a name first.";
    runNameInput.focus();
    return;
  }
  if (!prompt) {
    statusEl.textContent = "Enter a numeric scoring prompt first.";
    promptInput.focus();
    return;
  }
  if (!prompt.includes("COMPANY")) {
    statusEl.textContent = "Your prompt must include the COMPANY keyword.";
    promptInput.focus();
    return;
  }
  const stockList = selectedUniverse();
  const companyCount = Number(companyCountInput.value);
  const maximumCompanyCount = stockList ? stockList.company_count : companiesAvailable;
  const model = modelSelect.value;
  const reasoningMode = reasoningSelect.value;
  const maxTokens = Number(maxTokensInput.value);
  if (!model) {
    statusEl.textContent = "Choose a model first.";
    modelSelect.focus();
    return;
  }
  if (!reasoningMode) {
    statusEl.textContent = "Choose a reasoning mode first.";
    reasoningSelect.focus();
    return;
  }
  if (!Number.isInteger(maxTokens) || maxTokens < 1 || maxTokens > 32768) {
    statusEl.textContent = "Choose a response token limit from 1 to 32,768.";
    maxTokensInput.focus();
    return;
  }
  if (!Number.isInteger(companyCount) || companyCount < 1 || companyCount > maximumCompanyCount) {
    statusEl.textContent = `Choose a stock count from 1 to ${maximumCompanyCount}.`;
    companyCountInput.focus();
    return;
  }

  runButton.disabled = true;

  try {
    statusEl.textContent = "Estimating run cost...";
    const confirmed = await confirmCostEstimate({
      model,
      reasoningMode,
      companyCount,
      actionLabel: "Start this scoring run?",
    });
    if (!confirmed) {
      statusEl.textContent = "Run canceled before any AI requests were sent.";
      return;
    }

    statusEl.textContent = `Creating scoring run for ${companyCount} stocks...`;
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        prompt,
        companyCount,
        model,
        reasoningMode,
        maxTokens,
        stockListId: stockList
          ? stockList.kind === "snapshot"
            ? stockList.stock_list_id
            : stockList.id
          : null,
        ...(stockList?.kind === "snapshot" ? { tickers: stockList.tickers } : {}),
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not create run");

    statusEl.textContent = `Run #${payload.runId} started. Opening ranking...`;
    window.location.href = payload.url;
  } catch (error) {
    statusEl.textContent = error.message;
  } finally {
    runButton.disabled = false;
  }
}

runButton.addEventListener("click", createRun);
promptInput.addEventListener("input", resizePromptInput);
for (const tab of homeTabs) {
  tab.addEventListener("click", () => selectHomeTab(tab.dataset.homeTab));
  tab.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const currentIndex = homeTabs.indexOf(tab);
    const nextIndex =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? homeTabs.length - 1
          : (currentIndex + (event.key === "ArrowRight" ? 1 : -1) + homeTabs.length) % homeTabs.length;
    homeTabs[nextIndex].focus();
    selectHomeTab(homeTabs[nextIndex].dataset.homeTab);
  });
}
window.addEventListener("hashchange", () => selectHomeTab(activeTabFromHash(), false));
universeSelect.addEventListener("change", updateUniverseControls);
companyCountInput.addEventListener("input", () => {
  if (!selectedUniverse()) topCompanyCount = Number(companyCountInput.value) || topCompanyCount;
});
listEditorSelect.addEventListener("change", () => {
  if (listEditorSelect.value) openListEditor(listEditorSelect.value);
  else resetListEditor();
});
newListButton.addEventListener("click", resetListEditor);
saveListButton.addEventListener("click", saveCurrentStockList);
archiveListButton.addEventListener("click", archiveCurrentStockList);
stockSearchInput.addEventListener("input", renderStockPicker);
stockSearchResults.addEventListener("click", (event) => {
  const button = event.target.closest("[data-add-ticker]");
  if (!button || selectedTickers.includes(button.dataset.addTicker)) return;
  selectedTickers.push(button.dataset.addTicker);
  renderStockPicker();
});
selectedStocks.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-ticker]");
  if (!button) return;
  selectedTickers = selectedTickers.filter((ticker) => ticker !== button.dataset.removeTicker);
  renderStockPicker();
});
runRows.addEventListener("click", (event) => {
  const renameButton = event.target.closest("[data-rename-run]");
  if (renameButton) {
    event.preventDefault();
    renameRun(renameButton.dataset.renameRun, renameButton.dataset.runName);
    return;
  }

  const deleteButton = event.target.closest("[data-delete-run]");
  if (deleteButton) {
    event.preventDefault();
    deleteRun(deleteButton.dataset.deleteRun, deleteButton.dataset.runName);
    return;
  }

  const row = event.target.closest("tr[data-run-id]");
  if (row) {
    window.location.href = `/run.html?id=${encodeURIComponent(row.dataset.runId)}`;
  }
});

window.addEventListener("pageshow", (event) => {
  if (event.persisted) {
    loadRuns().catch((error) => {
      runsStatusEl.textContent = error.message;
      setRunRows("Saved runs could not be loaded. Refresh the page to try again.");
    });
  }
});

if ("EventSource" in window) {
  const events = new EventSource("/events");
  events.addEventListener("reload", () => window.location.reload());
}

try {
  selectHomeTab(editRunId ? "set-run" : activeTabFromHash(), Boolean(editRunId));
  setRunRows("Loading saved scoring runs...");
  await loadCompanies();
  await loadModels();
  await loadStockLists();
  if (editRunId) await prefillFromRun(editRunId);
  await loadRuns();
} catch (error) {
  statusEl.textContent = error.message;
  runsStatusEl.textContent = error.message;
  setRunRows("Saved runs could not be loaded. Refresh the page to try again.");
}
