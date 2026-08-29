import { DataTable, bindTablePagination } from "./data-table.js";

const promptInput = document.querySelector("#promptInput");
const runNameInput = document.querySelector("#runNameInput");
const modelSelect = document.querySelector("#modelSelect");
const reasoningSelect = document.querySelector("#reasoningSelect");
const universeSelect = document.querySelector("#universeSelect");
const companyCountInput = document.querySelector("#companyCountInput");
const maxTokensInput = document.querySelector("#maxTokensInput");
const minimumConfidenceInput = document.querySelector("#minimumConfidenceInput");
const confidenceFilterHelp = document.querySelector("#confidenceFilterHelp");
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
const manualRankingSelect = document.querySelector("#manualRankingSelect");
const manualRankingName = document.querySelector("#manualRankingName");
const manualRankingStockList = document.querySelector("#manualRankingStockList");
const createManualRankingButton = document.querySelector("#createManualRankingButton");
const archiveManualRankingButton = document.querySelector("#archiveManualRankingButton");
const manualRankingSearch = document.querySelector("#manualRankingSearch");
const manualRankingStatus = document.querySelector("#manualRankingStatus");
const manualRankingRows = document.querySelector("#manualRankingRows");
const runButton = document.querySelector("#runButton");
const statusEl = document.querySelector("#status");
const runsStatusEl = document.querySelector("#runsStatus");
const starredRunsStatusEl = document.querySelector("#starredRunsStatus");
const runRows = document.querySelector("#runRows");
const starredRunRows = document.querySelector("#starredRunRows");
const confidenceRunsStatus = document.querySelector("#confidenceRunsStatus");
const confidenceRunRows = document.querySelector("#confidenceRunRows");
const companiesSearchInput = document.querySelector("#companiesSearchInput");
const companiesStatus = document.querySelector("#companiesStatus");
const companyRows = document.querySelector("#companyRows");
const companiesPreviousButton = document.querySelector("#companiesPreviousButton");
const companiesNextButton = document.querySelector("#companiesNextButton");
const companiesPageStatus = document.querySelector("#companiesPageStatus");
const homeTabs = [...document.querySelectorAll("[data-home-tab]")];
const homePanels = [...document.querySelectorAll("[data-home-panel]")];
const pageParams = new URLSearchParams(window.location.search);
const editRunId = pageParams.get("editRun");
let companiesAvailable = 0;
let allCompanies = [];
const companyCache = new Map();
let stockPickerMatches = [];
let stockPickerPagination = { page: 0, page_size: 100, total: 0, total_pages: 1 };
let stockPickerLoading = false;
let stockPickerRequestGeneration = 0;
let companyPagination = { page: 1, page_size: 100, total: 0, total_pages: 1, offset: 0 };
let companiesSearchTimer = null;
let stockSearchTimer = null;
let stockLists = [];
let manualRankings = [];
let currentManualRanking = null;
let manualRankingSort = { key: "rank", direction: "asc" };
const manualScoreSaveTimers = new Map();
let editingListId = null;
let selectedTickers = [];
let topCompanyCount = Number(companyCountInput.value) || 10;
let runSnapshotUniverse = null;
let confidenceRuns = [];
let pinnedConfidenceRunId = null;
let companySort = { key: "rank", direction: "asc" };
let availableModels = [];

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

function setRunRows(message, target = runRows) {
  const controller = target === starredRunRows ? starredRunsTable : savedRunsTable;
  controller.setLoading(message);
}

function setRunsStatus(message) {
  runsStatusEl.textContent = message;
  starredRunsStatusEl.textContent = message;
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

const companyTable = new DataTable({
  table: document.querySelector(".companies-table"),
  body: companyRows,
  onSort: (key) => {
    companySort = companySort.key === key
      ? { key, direction: companySort.direction === "asc" ? "desc" : "asc" }
      : { key, direction: ["name", "country"].includes(key) ? "asc" : "desc" };
    companyPagination.page = 1;
    loadCompanies().catch((error) => { companiesStatus.textContent = error.message; });
  },
  columns: [
    { key: "position", label: "#", render: (_company, index) => companyPagination.offset + index + 1 },
    { key: "rank", label: "Market Cap Rank", sortKey: "rank", render: (company) => escapeHtml(company.rank) },
    {
      key: "company",
      label: "Company",
      sortKey: "name",
      render: (company) => {
        const logo = company.logo ? `<img class="logo" src="${escapeHtml(company.logo)}" alt="" loading="lazy" onerror="this.hidden=true" />` : "";
        return `<div class="company-cell">${logo}<div><strong>${escapeHtml(company.name)}</strong><span class="ticker">${escapeHtml(company.ticker)}</span></div></div>`;
      },
    },
    { key: "marketCap", label: "Market Cap", sortKey: "marketCapValue", render: (company) => escapeHtml(company.marketCap || "--") },
    { key: "price", label: "Price", render: (company) => escapeHtml(company.price || "--") },
    { key: "country", label: "Country", sortKey: "country", render: (company) => escapeHtml(company.country || "--") },
  ],
});

const manualRankingTable = new DataTable({
  table: document.querySelector(".manual-ranking-table"),
  body: manualRankingRows,
  onSort: (key) => {
    manualRankingSort = manualRankingSort.key === key
      ? { key, direction: manualRankingSort.direction === "asc" ? "desc" : "asc" }
      : { key, direction: key === "company" ? "asc" : "desc" };
    renderManualRanking();
  },
  columns: [
    { key: "rank", label: "Rank", sortKey: "rank", render: (company) => company.manual_rank ?? "--" },
    {
      key: "score",
      label: "Score",
      sortKey: "score",
      cellClass: "manual-score-cell",
      render: (company) => `<input class="manual-score-input" type="number" min="0" max="100" step="1" value="${company.score ?? ""}" placeholder="--" aria-label="Score ${escapeHtml(company.name)}" data-manual-score-ticker="${escapeHtml(company.ticker)}" />`,
    },
    {
      key: "company",
      label: "Company",
      sortKey: "company",
      render: (company) => {
        const logo = company.logo ? `<img class="logo" src="${escapeHtml(company.logo)}" alt="" loading="lazy" onerror="this.hidden=true" />` : "";
        return `<div class="company-cell">${logo}<div><strong>${escapeHtml(company.name)}</strong><span class="ticker">${escapeHtml(company.ticker)}</span></div></div>`;
      },
    },
    { key: "marketCap", label: "Market Cap", sortKey: "marketCap", render: (company) => escapeHtml(company.marketCap || "--") },
  ],
});

const updateCompaniesPagination = bindTablePagination({
  previousButton: companiesPreviousButton,
  nextButton: companiesNextButton,
  statusElement: companiesPageStatus,
  onPageChange: (page) => {
    companyPagination.page = page;
    loadCompanies().catch((error) => { companiesStatus.textContent = error.message; });
  },
});

const runColumns = [
  { key: "run", label: "Run", render: (run) => `#${run.id}` },
  { key: "name", label: "Name", render: (run) => `<strong>${escapeHtml(run.name || `Run #${run.id}`)}</strong>` },
  { key: "status", label: "Status", render: (run) => `<span class="${statusClass(run.status)}">${escapeHtml(run.status)}</span>` },
  { key: "progress", label: "Progress", render: (run) => progress(run) },
  { key: "cost", label: "Cost", render: (run) => formatCost(run.cost) },
  { key: "created", label: "Created", render: (run) => formatDate(run.created_at) },
  { key: "star", label: "Star", cellClass: "table-action-cell", render: (run) => `<button class="table-action-button" type="button" data-star-run="${run.id}" data-starred="${run.starred ? "false" : "true"}" data-run-name="${escapeHtml(run.name || `Run #${run.id}`)}">${run.starred ? "Unstar" : "Star"}</button>` },
  { key: "rename", label: "Rename", cellClass: "table-action-cell", render: (run) => `<button class="table-action-button" type="button" data-rename-run="${run.id}" data-run-name="${escapeHtml(run.name || `Run #${run.id}`)}">Rename</button>` },
  { key: "archive", label: "Archive", cellClass: "table-action-cell", render: (run) => `<button class="table-action-button table-action-button-danger" type="button" data-delete-run="${run.id}" data-run-name="${escapeHtml(run.name || `Run #${run.id}`)}">Archive</button>` },
];

function makeRunTable(table, body) {
  const controller = new DataTable({
    table,
    body,
    columns: runColumns,
  });
  controller.setContext({ rowClass: "clickable-row", rowAttributes: (run) => `data-run-id="${run.id}"` });
  return controller;
}

const savedRunsTable = makeRunTable(document.querySelector("#runsPanel table"), runRows);
const starredRunsTable = makeRunTable(document.querySelector("#starredRunsPanel table"), starredRunRows);

const confidenceRunsTable = new DataTable({
  table: document.querySelector(".confidence-table"),
  body: confidenceRunRows,
  columns: [
    { key: "run", label: "Run", render: (run) => `#${run.id}` },
    { key: "name", label: "Name", render: (run) => `<strong>${escapeHtml(run.name || `Run #${run.id}`)}</strong>` },
    { key: "status", label: "Status", render: (run) => `<span class="${statusClass(run.status)}">${escapeHtml(run.status)}</span>` },
    { key: "progress", label: "Progress", render: (run) => progress(run) },
    { key: "cost", label: "Cost", render: (run) => formatCost(run.cost) },
    { key: "created", label: "Created", render: (run) => formatDate(run.created_at) },
    { key: "pin", label: "Pin", cellClass: "table-action-cell", render: (run) => `<button class="table-action-button confidence-pin-button" type="button" data-pin-confidence-run="${run.id}" ${run.pinned ? "disabled" : ""}>${run.pinned ? "Pinned" : "Pin"}</button>` },
  ],
});
confidenceRunsTable.setContext({
  rowClass: "clickable-row",
  rowAttributes: (run) => `data-confidence-run-id="${run.id}"`,
});

function renderCompanies() {
  companiesStatus.textContent = companyPagination.total
    ? `${(companyPagination.offset + 1).toLocaleString()}-${Math.min(
        companyPagination.offset + allCompanies.length,
        companyPagination.total
      ).toLocaleString()} of ${companyPagination.total.toLocaleString()} companies.`
    : "No companies found.";
  updateCompaniesPagination({ page: companyPagination.page, totalPages: companyPagination.total_pages });
  companyTable.setSortState(companySort);
  companyTable.setRows(allCompanies, { emptyMessage: "No companies match this search." });
}

function updateConfidenceFilterState() {
  const pinned = confidenceRuns.find((run) => run.id === pinnedConfidenceRunId);
  minimumConfidenceInput.disabled = !pinned;
  confidenceFilterHelp.textContent = pinned
    ? `Using ${pinned.name || `confidence run #${pinned.id}`}. Stocks below the minimum are excluded before requests are queued.`
    : "Pin a confidence run to enable this filter.";
  if (!pinned) minimumConfidenceInput.value = "";
}

function renderConfidenceRuns() {
  if (!confidenceRuns.length) {
    confidenceRunsTable.setRows([], { emptyMessage: "No confidence score runs yet." });
    confidenceRunsStatus.textContent = "Run the confidence scoring program to create a dataset.";
    updateConfidenceFilterState();
    return;
  }
  const pinned = confidenceRuns.find((run) => run.id === pinnedConfidenceRunId);
  confidenceRunsStatus.textContent = pinned
    ? `${pinned.name || `Run #${pinned.id}`} is pinned for scoring filters and run-table confidence scores.`
    : "Pin one confidence run to use its scores in standard scoring runs.";
  confidenceRunsTable.setRows(confidenceRuns);
  updateConfidenceFilterState();
}

async function loadConfidenceRuns() {
  confidenceRunsStatus.textContent = "Loading confidence runs...";
  const payload = await fetchJson("/api/confidence-runs");
  confidenceRuns = payload.runs || [];
  pinnedConfidenceRunId = payload.pinnedRunId;
  renderConfidenceRuns();
}

async function pinConfidenceRun(runId) {
  confidenceRunsStatus.textContent = "Pinning confidence dataset...";
  const response = await fetch(`/api/confidence-runs/${encodeURIComponent(runId)}/pin`, { method: "POST" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Could not pin confidence run");
  await loadConfidenceRuns();
}

function companyByTicker(ticker) {
  return companyCache.get(ticker);
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
  const selected = new Set(selectedTickers);
  const matches = stockPickerMatches.filter((company) => !selected.has(company.ticker));
  const hasMore = stockPickerPagination.page < stockPickerPagination.total_pages;
  const progress = stockPickerLoading
    ? (stockPickerMatches.length ? "Loading more stocks..." : "Loading stocks...")
    : hasMore && stockPickerMatches.length
      ? `${stockPickerMatches.length.toLocaleString()} of ${stockPickerPagination.total.toLocaleString()} loaded. Scroll for more.`
      : stockPickerMatches.length
        ? `All ${stockPickerPagination.total.toLocaleString()} matching stocks loaded.`
        : "";

  stockSearchResults.innerHTML = [
    matches.length
      ? matches.map((company) => companyPickerRow(company, "add", "Add")).join("")
      : (!stockPickerLoading && !hasMore
        ? '<p class="stock-picker-empty">No matching stocks available.</p>'
        : ""),
    progress ? `<p class="stock-picker-progress">${escapeHtml(progress)}</p>` : "",
  ].join("");

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
  loadStockPickerMatches({ reset: true }).catch((error) => {
    stockSearchResults.innerHTML = `<p class="stock-picker-empty">${escapeHtml(error.message)}</p>`;
  });
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
  loadStockPickerMatches({ reset: true }).catch((error) => {
    stockSearchResults.innerHTML = `<p class="stock-picker-empty">${escapeHtml(error.message)}</p>`;
  });
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
  if (!cents) return "0.0000¢";
  return `${cents.toLocaleString(undefined, {
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  })}¢`;
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
    setRunsStatus("Run name is required.");
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
    setRunsStatus(`Renamed to ${payload.run.name}.`);
    await loadRuns();
  } catch (error) {
    setRunsStatus(error.message);
  }
}

async function toggleRunStar(runId, shouldStar, currentName) {
  const label = currentName || `Run #${runId}`;
  const scrollY = window.scrollY;
  setRunsStatus(`${shouldStar ? "Starring" : "Unstarring"} ${label}...`);
  try {
    const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ starred: shouldStar }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not update starred run");
    setRunsStatus(`${shouldStar ? "Starred" : "Unstarred"} ${label}.`);
    await loadRuns();
    requestAnimationFrame(() => window.scrollTo({ top: scrollY, left: 0 }));
  } catch (error) {
    setRunsStatus(error.message);
    requestAnimationFrame(() => window.scrollTo({ top: scrollY, left: 0 }));
  }
}

async function deleteRun(runId, currentName) {
  const label = currentName || `Run #${runId}`;
  const scrollY = window.scrollY;
  setRunsStatus(`Archiving ${label}...`);

  try {
    const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`, {
      method: "DELETE",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not archive run");
    setRunsStatus(`Archived ${label}.`);
    await loadRuns();
    requestAnimationFrame(() => window.scrollTo({ top: scrollY, left: 0 }));
  } catch (error) {
    setRunsStatus(error.message);
    requestAnimationFrame(() => window.scrollTo({ top: scrollY, left: 0 }));
  }
}

async function loadCompanies() {
  const query = new URLSearchParams({
    page: String(companyPagination.page),
    pageSize: "100",
    q: companiesSearchInput.value.trim(),
    sort: companySort.key,
    dir: companySort.direction,
  });
  const payload = await fetchJson(`/api/companies?${query.toString()}`);
  allCompanies = payload.companies;
  companyPagination = payload.pagination;
  companiesAvailable = companyPagination.total;
  for (const company of allCompanies) companyCache.set(company.ticker, company);
  companyCountInput.max = String(companiesAvailable);
  companyCountHelp.textContent = `Choose 1-${companiesAvailable}. Scoring starts from the largest companies by market cap.`;
  renderCompanies();
}

async function loadStockPickerMatches({ reset = false } = {}) {
  if (reset) {
    stockPickerRequestGeneration += 1;
    stockPickerMatches = [];
    stockPickerPagination = { page: 0, page_size: 100, total: 0, total_pages: 1 };
    stockPickerLoading = false;
  }
  if (stockPickerLoading || stockPickerPagination.page >= stockPickerPagination.total_pages) return;

  const requestGeneration = stockPickerRequestGeneration;
  const nextPage = stockPickerPagination.page + 1;
  stockPickerLoading = true;
  renderStockPicker();
  const query = new URLSearchParams({
    page: String(nextPage),
    pageSize: "100",
    q: stockSearchInput.value.trim(),
    sort: "rank",
    dir: "asc",
  });
  try {
    const payload = await fetchJson(`/api/companies?${query.toString()}`);
    if (requestGeneration !== stockPickerRequestGeneration) return;
    const existingTickers = new Set(stockPickerMatches.map((company) => company.ticker));
    const newCompanies = (payload.companies || []).filter(
      (company) => !existingTickers.has(company.ticker)
    );
    stockPickerMatches.push(...newCompanies);
    stockPickerPagination = payload.pagination || {
      page: nextPage,
      page_size: 100,
      total: stockPickerMatches.length,
      total_pages: nextPage,
    };
    for (const company of newCompanies) companyCache.set(company.ticker, company);
  } finally {
    if (requestGeneration === stockPickerRequestGeneration) {
      stockPickerLoading = false;
      renderStockPicker();
    }
  }
}

function loadNextStockPickerPageIfNeeded() {
  const distanceFromBottom =
    stockSearchResults.scrollHeight - stockSearchResults.scrollTop - stockSearchResults.clientHeight;
  if (distanceFromBottom > 120) return;
  loadStockPickerMatches().catch((error) => {
    stockSearchResults.insertAdjacentHTML(
      "beforeend",
      `<p class="stock-picker-empty">${escapeHtml(error.message)}</p>`
    );
  });
}

async function loadModels() {
  const payload = await fetchJson("/api/models");
  availableModels = payload.models || [];
  modelSelect.innerHTML = "";
  for (const model of availableModels) {
    const option = document.createElement("option");
    option.value = model.id;
    option.textContent = model.label;
    modelSelect.append(option);
  }
  modelSelect.value = payload.default;
  renderReasoningModes(payload.default_reasoning_mode);
  maxTokensInput.value = String(payload.default_max_tokens || 200);
}

function renderReasoningModes(preferredMode = null) {
  const model = availableModels.find((option) => option.id === modelSelect.value);
  const modes = model?.reasoning_modes || [];
  reasoningSelect.innerHTML = "";
  for (const mode of modes) {
    const option = document.createElement("option");
    option.value = mode.id;
    option.textContent = mode.label;
    reasoningSelect.append(option);
  }
  const validPreferredMode = modes.some((mode) => mode.id === preferredMode);
  reasoningSelect.value = validPreferredMode
    ? preferredMode
    : (model?.default_reasoning_mode || modes[0]?.id || "");
  reasoningSelect.disabled = modes.length <= 1;
}

async function loadStockLists(preferredUniverseValue = universeSelect.value) {
  const payload = await fetchJson("/api/stock-lists");
  stockLists = payload.lists || [];
  for (const stockList of stockLists) {
    for (const company of stockList.companies || []) companyCache.set(company.ticker, company);
  }
  renderStockListSelectors(preferredUniverseValue);
}

function renderManualRankingSelectors() {
  const selectedId = currentManualRanking?.id || Number(manualRankingSelect.value) || null;
  manualRankingSelect.innerHTML = '<option value="">Create a ranking</option>';
  for (const ranking of manualRankings) {
    const option = document.createElement("option");
    option.value = String(ranking.id);
    option.textContent = `${ranking.name} (${ranking.scored_count}/${ranking.company_count})`;
    manualRankingSelect.append(option);
  }
  manualRankingStockList.innerHTML = '<option value="">Choose a stock list</option>';
  for (const stockList of stockLists) {
    const option = document.createElement("option");
    option.value = String(stockList.id);
    option.textContent = `${stockList.name} (${stockList.company_count})`;
    manualRankingStockList.append(option);
  }
  manualRankingSelect.value = selectedId && manualRankings.some((ranking) => ranking.id === selectedId)
    ? String(selectedId)
    : "";
  manualRankingStockList.value = currentManualRanking?.stock_list_id
    ? String(currentManualRanking.stock_list_id)
    : "";
}

function manualSortValue(company, key) {
  if (key === "rank") return company.manual_rank ?? Number.MAX_SAFE_INTEGER;
  if (key === "score") return company.score === null ? -Infinity : Number(company.score);
  if (key === "company") return `${company.name} ${company.ticker}`.toLowerCase();
  if (key === "marketCap") return Number(company.marketCapValue || 0);
  return company.position;
}

function renderManualRanking() {
  manualRankingTable.setSortState(manualRankingSort);
  archiveManualRankingButton.disabled = !currentManualRanking;
  if (!currentManualRanking) {
    manualRankingTable.setRows([], { emptyMessage: "Create or select a manual ranking." });
    return;
  }
  const query = manualRankingSearch.value.trim().toLowerCase();
  const direction = manualRankingSort.direction === "desc" ? -1 : 1;
  const rows = currentManualRanking.companies
    .filter((company) => !query || `${company.name} ${company.ticker}`.toLowerCase().includes(query))
    .sort((left, right) => {
      const leftValue = manualSortValue(left, manualRankingSort.key);
      const rightValue = manualSortValue(right, manualRankingSort.key);
      return (leftValue < rightValue ? -1 : leftValue > rightValue ? 1 : left.position - right.position) * direction;
    });
  manualRankingTable.setRows(rows, { emptyMessage: "No stocks match this search." });
  manualRankingStatus.textContent = `${currentManualRanking.scored_count} of ${currentManualRanking.company_count} stocks scored.`;
}

async function loadManualRankings(preferredId = currentManualRanking?.id) {
  const payload = await fetchJson("/api/manual-rankings");
  manualRankings = payload.rankings || [];
  renderManualRankingSelectors();
  if (preferredId && manualRankings.some((ranking) => ranking.id === Number(preferredId))) {
    await openManualRanking(preferredId);
  } else {
    currentManualRanking = null;
    renderManualRanking();
  }
}

async function openManualRanking(rankingId) {
  if (!rankingId) {
    currentManualRanking = null;
    manualRankingName.value = "";
    manualRankingStockList.value = "";
    renderManualRanking();
    return;
  }
  manualRankingStatus.textContent = "Loading manual ranking...";
  const payload = await fetchJson(`/api/manual-rankings/${encodeURIComponent(rankingId)}`);
  currentManualRanking = payload.ranking;
  manualRankingSelect.value = String(currentManualRanking.id);
  manualRankingName.value = currentManualRanking.name;
  manualRankingStockList.value = String(currentManualRanking.stock_list_id);
  renderManualRanking();
}

async function createManualRanking() {
  const name = manualRankingName.value.trim();
  const stockListId = Number(manualRankingStockList.value);
  if (!name) {
    manualRankingStatus.textContent = "Give this manual ranking a name.";
    manualRankingName.focus();
    return;
  }
  if (!stockListId) {
    manualRankingStatus.textContent = "Choose a saved stock list.";
    manualRankingStockList.focus();
    return;
  }
  createManualRankingButton.disabled = true;
  try {
    const response = await fetch("/api/manual-rankings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, stockListId }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not create manual ranking");
    await loadManualRankings(payload.ranking.id);
    manualRankingStatus.textContent = `Created ${payload.ranking.name}. Enter scores from 0 to 100.`;
  } catch (error) {
    manualRankingStatus.textContent = error.message;
  } finally {
    createManualRankingButton.disabled = false;
  }
}

async function saveManualScore(ticker, score, input) {
  if (!currentManualRanking) return;
  input.disabled = true;
  try {
    const response = await fetch(
      `/api/manual-rankings/${currentManualRanking.id}/scores/${encodeURIComponent(ticker)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ score: score === "" ? null : Number(score) }),
      }
    );
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not save score");
    currentManualRanking = payload.ranking;
    const summary = manualRankings.find((ranking) => ranking.id === currentManualRanking.id);
    if (summary) {
      summary.name = currentManualRanking.name;
      summary.updated_at = currentManualRanking.updated_at;
      summary.scored_count = currentManualRanking.scored_count;
      summary.company_count = currentManualRanking.company_count;
    }
    renderManualRankingSelectors();
    renderManualRanking();
  } catch (error) {
    manualRankingStatus.textContent = error.message;
    input.disabled = false;
  }
}

async function archiveManualRanking() {
  if (!currentManualRanking) return;
  const archivedName = currentManualRanking.name;
  archiveManualRankingButton.disabled = true;
  try {
    const response = await fetch(`/api/manual-rankings/${currentManualRanking.id}`, { method: "DELETE" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not archive manual ranking");
    currentManualRanking = null;
    manualRankingName.value = "";
    manualRankingStockList.value = "";
    await loadManualRankings();
    manualRankingStatus.textContent = `Archived ${archivedName}.`;
  } catch (error) {
    manualRankingStatus.textContent = error.message;
    archiveManualRankingButton.disabled = false;
  }
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
  minimumConfidenceInput.value = run.minimum_confidence_score ?? "";

  if (![...modelSelect.options].some((option) => option.value === run.model)) {
    const option = document.createElement("option");
    option.value = run.model;
    option.textContent = run.model_details?.label || run.model;
    modelSelect.append(option);
    availableModels.push({
      id: run.model,
      label: run.model_details?.label || run.model,
      reasoning_modes: run.model_details?.reasoning_modes || [],
      default_reasoning_mode: run.model_details?.default_reasoning_mode,
    });
  }
  modelSelect.value = run.model;
  renderReasoningModes(run.model_details?.reasoning_mode || run.reasoning_mode);

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
  setRunRows("Loading starred scoring runs...", starredRunRows);
  const payload = await fetchJson("/api/runs");

  if (!payload.runs.length) {
    setRunRows("No saved scoring runs yet.");
    setRunRows("No starred runs yet.", starredRunRows);
    return;
  }

  renderRunTable(runRows, payload.runs, "No saved scoring runs yet.");
  renderRunTable(
    starredRunRows,
    payload.runs.filter((run) => run.starred),
    "No starred runs yet. Star a run from the Runs tab."
  );
}

function renderRunTable(target, runs, emptyMessage) {
  const controller = target === starredRunRows ? starredRunsTable : savedRunsTable;
  controller.setRows(runs, { emptyMessage });
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
  const minimumConfidenceScore = minimumConfidenceInput.value.trim() === ""
    ? null
    : Number(minimumConfidenceInput.value);
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
  if (
    minimumConfidenceScore !== null &&
    (!Number.isFinite(minimumConfidenceScore) || minimumConfidenceScore < 0 || minimumConfidenceScore > 100)
  ) {
    statusEl.textContent = "Choose a minimum confidence score from 0 to 100.";
    minimumConfidenceInput.focus();
    return;
  }
  if (!Number.isInteger(companyCount) || companyCount < 1 || companyCount > maximumCompanyCount) {
    statusEl.textContent = `Choose a stock count from 1 to ${maximumCompanyCount}.`;
    companyCountInput.focus();
    return;
  }

  runButton.disabled = true;

  try {
    const selectionPayload = {
      companyCount,
      minimumConfidenceScore,
      stockListId: stockList
        ? stockList.kind === "snapshot"
          ? stockList.stock_list_id
          : stockList.id
        : null,
      ...(stockList?.kind === "snapshot" ? { tickers: stockList.tickers } : {}),
    };
    statusEl.textContent = "Applying confidence filter...";
    const previewResponse = await fetch("/api/run-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(selectionPayload),
    });
    const previewPayload = await previewResponse.json();
    if (!previewResponse.ok) throw new Error(previewPayload.error || "Could not preview run");
    const preview = previewPayload.preview;
    if (!preview.eligible_count) throw new Error("No selected stocks meet the minimum confidence score.");
    statusEl.textContent = "Estimating run cost...";
    const confirmed = await confirmCostEstimate({
      model,
      reasoningMode,
      companyCount: preview.eligible_count,
      actionLabel: preview.excluded_count
        ? `Start this scoring run? ${preview.excluded_count} stocks will be excluded by the confidence filter.`
        : "Start this scoring run?",
    });
    if (!confirmed) {
      statusEl.textContent = "Run canceled before any AI requests were sent.";
      return;
    }

    statusEl.textContent = `Creating scoring run for ${preview.eligible_count} eligible stocks...`;
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
        minimumConfidenceScore,
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
modelSelect.addEventListener("change", () => renderReasoningModes());
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
manualRankingSelect.addEventListener("change", () => {
  openManualRanking(manualRankingSelect.value).catch((error) => {
    manualRankingStatus.textContent = error.message;
  });
});
createManualRankingButton.addEventListener("click", createManualRanking);
archiveManualRankingButton.addEventListener("click", archiveManualRanking);
manualRankingSearch.addEventListener("input", renderManualRanking);
manualRankingRows.addEventListener("input", (event) => {
  const input = event.target.closest("[data-manual-score-ticker]");
  if (!input) return;
  const ticker = input.dataset.manualScoreTicker;
  window.clearTimeout(manualScoreSaveTimers.get(ticker));
  manualScoreSaveTimers.set(ticker, window.setTimeout(() => {
    manualScoreSaveTimers.delete(ticker);
    saveManualScore(ticker, input.value, input);
  }, 350));
});
manualRankingRows.addEventListener("change", (event) => {
  const input = event.target.closest("[data-manual-score-ticker]");
  if (!input) return;
  const ticker = input.dataset.manualScoreTicker;
  window.clearTimeout(manualScoreSaveTimers.get(ticker));
  manualScoreSaveTimers.delete(ticker);
  saveManualScore(ticker, input.value, input);
});
stockSearchInput.addEventListener("input", () => {
  window.clearTimeout(stockSearchTimer);
  stockPickerRequestGeneration += 1;
  stockPickerLoading = false;
  stockPickerMatches = [];
  stockPickerPagination = { page: 0, page_size: 100, total: 0, total_pages: 1 };
  renderStockPicker();
  stockSearchTimer = window.setTimeout(() => loadStockPickerMatches({ reset: true }).catch((error) => {
    stockSearchResults.innerHTML = `<p class="stock-picker-empty">${escapeHtml(error.message)}</p>`;
  }), 250);
});
stockSearchResults.addEventListener("scroll", loadNextStockPickerPageIfNeeded);
companiesSearchInput.addEventListener("input", () => {
  window.clearTimeout(companiesSearchTimer);
  companiesSearchTimer = window.setTimeout(() => {
    companyPagination.page = 1;
    loadCompanies().catch((error) => { companiesStatus.textContent = error.message; });
  }, 250);
});
confidenceRunRows.addEventListener("click", (event) => {
  const pinButton = event.target.closest("[data-pin-confidence-run]");
  if (pinButton) {
    event.preventDefault();
    pinConfidenceRun(pinButton.dataset.pinConfidenceRun).catch((error) => {
      confidenceRunsStatus.textContent = error.message;
    });
    return;
  }
  const row = event.target.closest("[data-confidence-run-id]");
  if (row) window.location.href = `/run.html?id=${encodeURIComponent(row.dataset.confidenceRunId)}`;
});
stockSearchResults.addEventListener("click", (event) => {
  const button = event.target.closest("[data-add-ticker]");
  if (!button || selectedTickers.includes(button.dataset.addTicker)) return;
  selectedTickers.push(button.dataset.addTicker);
  renderStockPicker();
  window.requestAnimationFrame(loadNextStockPickerPageIfNeeded);
});
selectedStocks.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-ticker]");
  if (!button) return;
  selectedTickers = selectedTickers.filter((ticker) => ticker !== button.dataset.removeTicker);
  renderStockPicker();
});
function handleRunTableClick(event) {
  const starButton = event.target.closest("[data-star-run]");
  if (starButton) {
    event.preventDefault();
    toggleRunStar(
      starButton.dataset.starRun,
      starButton.dataset.starred === "true",
      starButton.dataset.runName
    );
    return;
  }

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
}

runRows.addEventListener("click", handleRunTableClick);
starredRunRows.addEventListener("click", handleRunTableClick);

window.addEventListener("pageshow", (event) => {
  if (event.persisted) {
    loadRuns().catch((error) => {
      setRunsStatus(error.message);
      setRunRows("Saved runs could not be loaded. Refresh the page to try again.");
      setRunRows("Starred runs could not be loaded. Refresh the page to try again.", starredRunRows);
    });
    loadConfidenceRuns().catch((error) => {
      confidenceRunsStatus.textContent = error.message;
      confidenceRunsTable.setRows([], { emptyMessage: "Confidence runs could not be loaded." });
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
  setRunRows("Loading starred scoring runs...", starredRunRows);
  await loadCompanies();
  await loadStockPickerMatches({ reset: true });
  await loadModels();
  await loadStockLists();
  await loadManualRankings();
  if (editRunId) await prefillFromRun(editRunId);
  await loadConfidenceRuns();
  await loadRuns();
} catch (error) {
  statusEl.textContent = error.message;
  setRunsStatus(error.message);
  setRunRows("Saved runs could not be loaded. Refresh the page to try again.");
  setRunRows("Starred runs could not be loaded. Refresh the page to try again.", starredRunRows);
  confidenceRunsStatus.textContent = error.message;
  confidenceRunsTable.setRows([], { emptyMessage: "Confidence runs could not be loaded." });
}
