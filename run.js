const params = new URLSearchParams(window.location.search);
const stopButton = document.querySelector("#stopButton");
const rerunButton = document.querySelector("#rerunButton");
const editRunButton = document.querySelector("#editRunButton");
const copyRunButton = document.querySelector("#copyRunButton");
const extendButton = document.querySelector("#extendButton");
const fillButton = document.querySelector("#fillButton");
const redriveFailedButton = document.querySelector("#redriveFailedButton");
const deleteButton = document.querySelector("#deleteButton");
const runEditor = document.querySelector("#runEditor");
const runEditorName = document.querySelector("#runEditorName");
const runEditorPrompt = document.querySelector("#runEditorPrompt");
const runEditorMaxTokens = document.querySelector("#runEditorMaxTokens");
const runEditorUniverse = document.querySelector("#runEditorUniverse");
const runEditorUniverseHelp = document.querySelector("#runEditorUniverseHelp");
const saveRunButton = document.querySelector("#saveRunButton");
const cancelRunEditButton = document.querySelector("#cancelRunEditButton");
const runTitle = document.querySelector("#runTitle");
const runPrompt = document.querySelector("#runPrompt");
const runStatus = document.querySelector("#runStatus");
const runCount = document.querySelector("#runCount");
const statusEl = document.querySelector("#status");
const resultRows = document.querySelector("#resultRows");
const statModel = document.querySelector("#statModel");
const statProgress = document.querySelector("#statProgress");
const statQueueCount = document.querySelector("#statQueueCount");
const statEta = document.querySelector("#statEta");
const statCost = document.querySelector("#statCost");
const statTokens = document.querySelector("#statTokens");
const statTokenLimit = document.querySelector("#statTokenLimit");
const statAverageResponseTokens = document.querySelector("#statAverageResponseTokens");
const statAverageReasoningTokens = document.querySelector("#statAverageReasoningTokens");
const statTokenLimitRisk = document.querySelector("#statTokenLimitRisk");
const statTokenLimitRiskNote = document.querySelector("#statTokenLimitRiskNote");
const statLatency = document.querySelector("#statLatency");
const statScoreRange = document.querySelector("#statScoreRange");
const statAverageScore = document.querySelector("#statAverageScore");
const statMedianScore = document.querySelector("#statMedianScore");
const statRequests = document.querySelector("#statRequests");
const scoreTargetInput = document.querySelector("#scoreTargetInput");
const scoreDownButton = document.querySelector("#scoreDownButton");
const scoreUpButton = document.querySelector("#scoreUpButton");
const clearScoreFilterButton = document.querySelector("#clearScoreFilterButton");
const filterStatus = document.querySelector("#filterStatus");
const rankingSearchInput = document.querySelector("#rankingSearchInput");
const scoreFilterToolbar = document.querySelector("#scoreFilterToolbar");
const resultsTableWrap = document.querySelector("#resultsTableWrap");
const rankingTable = document.querySelector(".ranking-table");
const rankingTabCount = document.querySelector("#rankingTabCount");
const failedTabCount = document.querySelector("#failedTabCount");
const failedActions = document.querySelector("#failedActions");
const columnSelector = document.querySelector("#columnSelector");
const resetColumnsButton = document.querySelector("#resetColumnsButton");
const errorColumnOption = document.querySelector("#errorColumnOption");
const resultsPreviousButton = document.querySelector("#resultsPreviousButton");
const resultsNextButton = document.querySelector("#resultsNextButton");
const resultsPageStatus = document.querySelector("#resultsPageStatus");

const SORT_KEYS = new Set([
  "scoreRank",
  "score",
  "scorePercentile",
  "confidence",
  "company",
  "marketCap",
  "inputTokens",
  "responseTokens",
  "reasoningTokens",
  "totalTokens",
  "tokenBudgetPercent",
  "durationMs",
  "cost",
  "error",
]);
const SORT_DIRECTIONS = new Set(["asc", "desc"]);
const RESULT_VIEWS = new Set(["ranking", "failed"]);
const COLUMN_KEYS = [
  "rank",
  "score",
  "scorePercentile",
  "confidence",
  "company",
  "marketCap",
  "input",
  "response",
  "reasoning",
  "total",
  "budget",
  "time",
  "cost",
  "error",
  "search",
  "chart",
  "dashboard",
  "actions",
];
const LEGACY_COLUMN_STORAGE_KEY = "ai-stock-scorer-visible-run-columns-v6";
const COLUMN_STORAGE_KEYS = {
  ranking: "ai-stock-scorer-visible-ranking-columns-v1",
  failed: "ai-stock-scorer-visible-failed-columns-v1",
};

function loadVisibleColumns(view) {
  try {
    const stored =
      window.localStorage.getItem(COLUMN_STORAGE_KEYS[view]) ||
      window.localStorage.getItem(LEGACY_COLUMN_STORAGE_KEY);
    const saved = JSON.parse(stored);
    if (Array.isArray(saved)) {
      const valid = saved.filter((column) => COLUMN_KEYS.includes(column));
      if (valid.length) return new Set(valid);
    }
  } catch (_error) {
    // Use the complete default set when browser storage is unavailable or malformed.
  }
  return new Set(COLUMN_KEYS);
}

let currentRunId = params.get("id");
let pollTimer = null;
let etaTimer = null;
let currentRun = null;
let initialRunEditorUniverse = "top";
let etaState = null;
const requestedSortKey = params.get("sort") === "outputTokens" ? "responseTokens" : params.get("sort");
let sortState = {
  key: SORT_KEYS.has(requestedSortKey) ? requestedSortKey : "scoreRank",
  direction: SORT_DIRECTIONS.has(params.get("dir")) ? params.get("dir") : "asc",
};
let scoreFilterTarget =
  params.has("score") && Number.isFinite(Number(params.get("score"))) ? Number(params.get("score")) : null;
let rankingSearchQuery = (params.get("q") || "").trim();
let matchedScore = null;
let restoredScroll = false;
let activeResultView = RESULT_VIEWS.has(params.get("tab")) ? params.get("tab") : "ranking";
let currentPage = Math.max(1, Number(params.get("page")) || 1);
let resultFilterTimer = null;
let loadSequence = 0;
const visibleColumnsByView = {
  ranking: loadVisibleColumns("ranking"),
  failed: loadVisibleColumns("failed"),
};
let visibleColumns = visibleColumnsByView[activeResultView];
const columnSaveTimers = { ranking: null, failed: null };

function saveVisibleColumnsLocally(view, columns) {
  try {
    window.localStorage.setItem(COLUMN_STORAGE_KEYS[view], JSON.stringify([...columns]));
  } catch (_error) {
    // The selection still works for this page when browser storage is unavailable.
  }
}

async function persistVisibleColumns(view, columns) {
  const response = await fetch(`/api/preferences/run-table-columns?view=${encodeURIComponent(view)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ columns: [...columns] }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || "Unable to save table columns.");
  }
}

function saveVisibleColumns() {
  const view = activeResultView;
  const columns = new Set(visibleColumns);
  visibleColumnsByView[view] = columns;
  visibleColumns = columns;
  saveVisibleColumnsLocally(view, columns);
  if (columnSaveTimers[view]) window.clearTimeout(columnSaveTimers[view]);
  columnSaveTimers[view] = window.setTimeout(() => {
    persistVisibleColumns(view, columns).catch(() => {
      // Browser storage remains a usable fallback if the server is unavailable.
    });
  }, 200);
}

async function loadPersistedVisibleColumns() {
  await Promise.all(
    [...RESULT_VIEWS].map(async (view) => {
      try {
        const response = await fetch(
          `/api/preferences/run-table-columns?view=${encodeURIComponent(view)}`
        );
        if (!response.ok) throw new Error("Unable to load table columns.");
        const payload = await response.json();
        if (Array.isArray(payload.columns)) {
          const valid = payload.columns.filter((column) => COLUMN_KEYS.includes(column));
          if (valid.length) {
            visibleColumnsByView[view] = new Set(valid);
            saveVisibleColumnsLocally(view, visibleColumnsByView[view]);
            return;
          }
        }
        await persistVisibleColumns(view, visibleColumnsByView[view]);
      } catch (_error) {
        // Keep the local selection when persistent preferences cannot be reached.
      }
    })
  );
  visibleColumns = visibleColumnsByView[activeResultView];
  applyColumnVisibility();
}

function applyColumnVisibility() {
  document.querySelectorAll("[data-column]").forEach((element) => {
    element.classList.toggle("is-column-hidden", !visibleColumns.has(element.dataset.column));
  });
  document.querySelectorAll("[data-column-toggle]").forEach((input) => {
    input.checked = visibleColumns.has(input.dataset.columnToggle);
  });
  const emptyCell = resultRows.querySelector("[data-empty-results]");
  if (emptyCell) emptyCell.colSpan = visibleTableColumnCount();
}

function visibleTableColumnCount() {
  const visibleDataColumns = [...visibleColumns].filter(
    (column) => activeResultView === "failed" || column !== "error"
  ).length;
  return 1 + visibleDataColumns + (activeResultView === "failed" ? 1 : 0);
}

function renderEmptyResults(message) {
  resultRows.innerHTML = `<tr><td data-empty-results colspan="${visibleTableColumnCount()}">${escapeHtml(message)}</td></tr>`;
}

function hasVisibleColumnForCurrentView() {
  return [...visibleColumns].some(
    (column) => activeResultView === "failed" || column !== "error"
  );
}

function updateVisibleColumn(event) {
  const input = event.target.closest("[data-column-toggle]");
  if (!input) return;
  const column = input.dataset.columnToggle;
  if (input.checked) visibleColumns.add(column);
  else visibleColumns.delete(column);
  if (!hasVisibleColumnForCurrentView()) {
    visibleColumns.add(column);
    input.checked = true;
    statusEl.textContent = "Keep at least one table column visible.";
    return;
  }
  saveVisibleColumns();
  applyColumnVisibility();
}

function resetVisibleColumns() {
  visibleColumns = new Set(COLUMN_KEYS);
  visibleColumnsByView[activeResultView] = visibleColumns;
  saveVisibleColumns();
  applyColumnVisibility();
}

function ensureHomeLink() {
  let nav = document.querySelector(".page-nav");
  const shell = document.querySelector(".shell");
  if (!nav && shell) {
    nav = document.createElement("nav");
    nav.className = "page-nav";
    nav.setAttribute("aria-label", "Page navigation");
    shell.prepend(nav);
  }
  if (!nav) return;

  const existingHome = document.querySelector("[data-home-button]");
  if (existingHome) {
    existingHome.textContent = "Home";
    existingHome.classList.add("secondary-button");
    existingHome.classList.add("nav-link");
    if (existingHome.parentElement !== nav) nav.append(existingHome);
    if (!existingHome.dataset.navigationBound) {
      existingHome.addEventListener("click", () => {
        window.location.href = "/";
      });
      existingHome.dataset.navigationBound = "true";
    }
    return;
  }

  const homeButton = document.createElement("button");
  homeButton.type = "button";
  homeButton.className = "secondary-button nav-link";
  homeButton.dataset.homeButton = "";
  homeButton.textContent = "Home";
  homeButton.addEventListener("click", () => {
    window.location.href = "/";
  });
  homeButton.dataset.navigationBound = "true";
  nav.append(homeButton);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatScore(score) {
  if (score === null || score === undefined) return "";
  return Number(score).toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function formatCents(value) {
  const cents = Number(value || 0) * 100;
  if (!cents) return "0.0000¢";
  return `${cents.toLocaleString(undefined, {
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  })}¢`;
}

function formatCompactCents(value) {
  const cents = Number(value || 0) * 100;
  return `${cents.toLocaleString(undefined, {
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  })}¢`;
}

function formatPercent(value) {
  const number = Number(value);
  if (value === null || value === undefined || !Number.isFinite(number)) return "--";
  return `${number.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
}

function formatWholePercent(value) {
  const number = Number(value);
  if (value === null || value === undefined || !Number.isFinite(number)) return "--";
  return `${number.toLocaleString(undefined, { maximumFractionDigits: 0 })}%`;
}

function formatMarketCap(value, fallback = "") {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return fallback || "--";
  const units = [
    [1e12, "T"],
    [1e9, "B"],
    [1e6, "M"],
  ];
  const [divisor, suffix] = units.find(([threshold]) => number >= threshold) || [1, ""];
  return `$ ${(number / divisor).toLocaleString(undefined, { maximumFractionDigits: 2 })}${
    suffix ? ` ${suffix}` : ""
  }`;
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Could not load ${url}`);
  return payload;
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
      `Estimated cost: ${formatCents(estimate.estimated_cost)}\n` +
      `Average per stock: ${formatCents(estimate.average_request_cost)}\n\n` +
      `${sampleText}\n\nContinue?`
  );
}

function formatNumber(value) {
  if (value === null || value === undefined) return "--";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function formatMs(value) {
  if (value === null || value === undefined) return "--";
  const seconds = Number(value) / 1000;
  return `${seconds.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} seconds`;
}

function formatCompactSeconds(value) {
  if (value === null || value === undefined) return "--";
  return `${(Number(value) / 1000).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}s`;
}

function formatDuration(ms) {
  if (ms === null || ms === undefined) return "--";
  const seconds = Math.max(0, Math.ceil(Number(ms) / 1000));
  if (seconds < 60) return `${seconds} sec`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  if (minutes < 60) {
    return remainingSeconds ? `${minutes} min ${remainingSeconds} sec` : `${minutes} min`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes ? `${hours} hr ${remainingMinutes} min` : `${hours} hr`;
}

function numericValue(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function runUrlWithState(scrollY = window.scrollY) {
  const url = new URL("/run.html", window.location.origin);
  if (currentRunId) url.searchParams.set("id", currentRunId);
  url.searchParams.set("sort", sortState.key);
  url.searchParams.set("dir", sortState.direction);
  url.searchParams.set("tab", activeResultView);
  if (currentPage > 1) url.searchParams.set("page", String(currentPage));
  if (scoreFilterTarget !== null) url.searchParams.set("score", String(scoreFilterTarget));
  if (rankingSearchQuery) url.searchParams.set("q", rankingSearchQuery);
  url.searchParams.set("y", String(Math.max(0, Math.round(scrollY))));
  return `${url.pathname}${url.search}`;
}

function resultUrl(result, scrollY = window.scrollY) {
  const url = new URL("/result.html", window.location.origin);
  url.searchParams.set("run", currentRunId);
  url.searchParams.set("ticker", result.ticker);
  url.searchParams.set("sort", sortState.key);
  url.searchParams.set("dir", sortState.direction);
  url.searchParams.set("tab", activeResultView);
  if (currentPage > 1) url.searchParams.set("page", String(currentPage));
  if (scoreFilterTarget !== null) url.searchParams.set("score", String(scoreFilterTarget));
  if (rankingSearchQuery) url.searchParams.set("q", rankingSearchQuery);
  url.searchParams.set("y", String(Math.max(0, Math.round(scrollY))));
  return `${url.pathname}${url.search}`;
}

function responseUrl(result, scrollY = window.scrollY) {
  const url = new URL("/response.html", window.location.origin);
  url.searchParams.set("run", currentRunId);
  url.searchParams.set("ticker", result.ticker);
  url.searchParams.set("sort", sortState.key);
  url.searchParams.set("dir", sortState.direction);
  url.searchParams.set("tab", activeResultView);
  if (currentPage > 1) url.searchParams.set("page", String(currentPage));
  if (scoreFilterTarget !== null) url.searchParams.set("score", String(scoreFilterTarget));
  if (rankingSearchQuery) url.searchParams.set("q", rankingSearchQuery);
  url.searchParams.set("y", String(Math.max(0, Math.round(scrollY))));
  return `${url.pathname}${url.search}`;
}

function saveRunViewState() {
  history.replaceState(null, "", runUrlWithState());
}

function restoreScrollPosition() {
  if (restoredScroll) return;
  restoredScroll = true;
  const y = Number(params.get("y"));
  if (!Number.isFinite(y) || y <= 0) return;

  window.scrollTo(0, y);
}

function progress(run) {
  return `${run.completed_count + run.failed_count}/${run.company_count}`;
}

function canStop(run) {
  return run && ["queued", "running", "stop_requested"].includes(run.status);
}

function incompleteCount(run) {
  if (!run) return 0;
  if (Number.isFinite(Number(run.incomplete_count))) return Number(run.incomplete_count);
  const completedTickers = new Set(
    (run.results || [])
      .filter((result) => result.score !== null && result.score !== undefined && !result.error)
      .map((result) => result.ticker)
  );
  return Math.max(0, Number(run.company_count || 0) - completedTickers.size);
}

function etaEstimateMs(run) {
  if (!canStop(run)) return null;
  const remaining = incompleteCount(run);
  if (!remaining) return 0;
  const stats = run.stats || {};
  const averageLatencyMs = Number(stats.average_latency_ms || stats.recent_average_latency_ms || 0);
  if (!Number.isFinite(averageLatencyMs) || averageLatencyMs <= 0) return null;
  const concurrency = Math.max(1, Number(run.scoring_concurrency || 1));
  return Math.ceil(remaining / concurrency) * averageLatencyMs;
}

function updateEtaState(run) {
  if (!canStop(run)) {
    etaState = null;
    return;
  }

  const remaining = incompleteCount(run);
  const estimateMs = etaEstimateMs(run);
  const key = `${run.id}:${remaining}`;
  if (!etaState || etaState.key !== key || etaState.estimateMs !== estimateMs) {
    etaState = {
      key,
      estimateMs,
      targetAt: estimateMs === null ? null : Date.now() + estimateMs,
    };
  }
}

function renderEta(run = currentRun) {
  if (!statEta || !run) return;
  if (!canStop(run)) {
    statEta.textContent = incompleteCount(run) ? "Paused" : "Done";
    return;
  }

  if (!etaState || etaState.estimateMs === null) {
    statEta.textContent = "Calculating...";
    return;
  }

  const remainingMs = Math.max(0, etaState.targetAt - Date.now());
  statEta.textContent = formatDuration(remainingMs);
}

function startEtaTimer() {
  if (etaTimer) return;
  etaTimer = window.setInterval(() => renderEta(), 1000);
}

function numericScores(run) {
  return run.results
    .map((result) => result.score)
    .filter((score) => score !== null && score !== undefined)
    .map(Number)
    .filter((score) => Number.isFinite(score));
}

function renderRunStats(run) {
  const stats = run.stats || {};
  const model = run.model_details || {};
  const reasoning = model.reasoning || {};
  const provider = model.provider || {};
  const scores = numericScores(run);
  const scoreStats = run.score_stats || {};
  const minScore = scoreStats.minimum ?? (scores.length ? Math.min(...scores) : null);
  const maxScore = scoreStats.maximum ?? (scores.length ? Math.max(...scores) : null);
  const averageScore = scoreStats.average ?? (scores.length
    ? scores.reduce((sum, score) => sum + score, 0) / scores.length
    : null);
  const sortedScores = [...scores].sort((left, right) => left - right);
  const middleScoreIndex = Math.floor(sortedScores.length / 2);
  const medianScore = scoreStats.median ?? (sortedScores.length
    ? sortedScores.length % 2
      ? sortedScores[middleScoreIndex]
      : (sortedScores[middleScoreIndex - 1] + sortedScores[middleScoreIndex]) / 2
    : null);

  statModel.innerHTML = `
    <span class="model-label">${escapeHtml(model.label || run.model)}</span>
    <span class="model-id">${escapeHtml(model.id || run.model)}</span>
    <span class="model-id">mode: ${escapeHtml(model.reasoning_label || run.reasoning_mode || "Non-reasoning")}</span>
    <span class="model-id">reasoning: ${escapeHtml(reasoning.effort || "none")}, exclude: ${escapeHtml(
    reasoning.exclude === undefined ? "true" : String(reasoning.exclude)
  )}</span>
    <span class="model-id">response limit: ${escapeHtml(formatNumber(run.max_tokens))} tokens</span>
    <span class="model-id">provider routing: all except blocked</span>
    <span class="model-id">blocked: ${escapeHtml((provider.ignore || []).join(", ") || "none")}</span>
  `;
  statProgress.textContent = progress(run);
  statQueueCount.textContent = formatNumber(run.queue_count || 0);
  updateEtaState(run);
  renderEta(run);
  statCost.textContent = formatCents(stats.cost);
  statTokens.textContent = formatNumber(stats.total_tokens);
  statTokenLimit.textContent = formatNumber(run.max_tokens);
  statAverageResponseTokens.textContent = formatNumber(stats.average_response_tokens);
  statAverageReasoningTokens.textContent = formatNumber(stats.average_reasoning_tokens);
  const riskSampleSize = Number(stats.token_limit_risk_sample_size || 0);
  const riskOneIn = Number(stats.token_limit_risk_one_in);
  if (Number.isFinite(riskOneIn) && riskOneIn > 0) {
    statTokenLimitRisk.textContent = `${stats.token_limit_risk_capped ? ">" : ""}${formatNumber(
      riskOneIn
    )}`;
    statTokenLimitRiskNote.textContent = `1 expected token-limit failure per ${
      stats.token_limit_risk_capped ? "more than " : ""
    }${formatNumber(riskOneIn)} stocks · ${formatNumber(riskSampleSize)} successful samples`;
  } else {
    statTokenLimitRisk.textContent = "--";
    statTokenLimitRiskNote.textContent = `Needs 10 successful stocks · ${formatNumber(
      riskSampleSize
    )} available`;
  }
  statLatency.textContent = formatMs(stats.average_latency_ms);
  statScoreRange.textContent =
    minScore === null ? "--" : `${formatScore(minScore)}-${formatScore(maxScore)}`;
  statAverageScore.textContent = averageScore === null ? "--" : formatScore(averageScore);
  statMedianScore.textContent = medianScore === null ? "--" : formatScore(medianScore);
  statRequests.textContent = `${formatNumber(stats.successful_request_count || 0)} ok / ${formatNumber(
    stats.failed_request_count || 0
  )} failed`;
}

function sortValue(result, key) {
  if (key === "scoreRank") return result.scoreRank;
  if (key === "score") return numericValue(result.score);
  if (key === "scorePercentile") return numericValue(result.score_percentile);
  if (key === "confidence") return numericValue(result.confidence_score);
  if (key === "company") return `${result.company_name || ""} ${result.ticker || ""}`.toLowerCase();
  if (key === "marketCap") return numericValue(result.market_cap_value);
  if (key === "inputTokens") return numericValue(result.prompt_tokens);
  if (key === "responseTokens") return numericValue(result.response_tokens);
  if (key === "reasoningTokens") return numericValue(result.reasoning_tokens);
  if (key === "totalTokens") return numericValue(result.total_tokens);
  if (key === "tokenBudgetPercent") return numericValue(result.token_budget_used_percent);
  if (key === "durationMs") return numericValue(result.duration_ms);
  if (key === "cost") return numericValue(result.cost);
  if (key === "error") return (result.error || "").toLowerCase();
  return result.scoreRank;
}

function compareResults(left, right, key, direction) {
  const leftValue = sortValue(left, key);
  const rightValue = sortValue(right, key);
  const leftMissing = leftValue === null || leftValue === "";
  const rightMissing = rightValue === null || rightValue === "";

  if (leftMissing && rightMissing) return left.scoreRank - right.scoreRank;
  if (leftMissing) return 1;
  if (rightMissing) return -1;

  let comparison = 0;
  if (typeof leftValue === "number" && typeof rightValue === "number") {
    comparison = leftValue - rightValue;
  } else {
    comparison = String(leftValue).localeCompare(String(rightValue), undefined, {
      numeric: true,
      sensitivity: "base",
    });
  }

  if (direction === "desc") comparison *= -1;
  return comparison || left.scoreRank - right.scoreRank;
}

function sortedResults(results) {
  return results
    .map((result, index) => ({ ...result, scoreRank: index + 1 }))
    .sort((left, right) => {
      return compareResults(left, right, sortState.key, sortState.direction);
    });
}

function scoreInFilter(result) {
  if (scoreFilterTarget === null) return true;
  if (matchedScore === null) return false;
  const score = numericValue(result.score);
  return score !== null && score === matchedScore;
}

function filteredResults(results) {
  const query = rankingSearchQuery.toLowerCase();
  return results.filter(scoreInFilter).filter((result) => {
    if (!query) return true;
    return `${result.company_name || ""} ${result.ticker || ""}`.toLowerCase().includes(query);
  });
}

function isSuccessfulResult(result) {
  return result.score !== null && result.score !== undefined && !result.error;
}

function resultsForView(run, view = activeResultView) {
  const results = run?.results || [];
  return view === "failed"
    ? results.filter((result) => !isSuccessfulResult(result))
    : results.filter(isSuccessfulResult);
}

function updateResultViewTabs(run) {
  if (!hasVisibleColumnForCurrentView()) {
    visibleColumns.add("company");
    saveVisibleColumns();
  }
  const counts = run.result_page?.counts;
  rankingTabCount.textContent = String(counts?.ranking ?? resultsForView(run, "ranking").length);
  failedTabCount.textContent = String(counts?.failed ?? resultsForView(run, "failed").length);

  document.querySelectorAll("[data-result-view]").forEach((button) => {
    const isActive = button.dataset.resultView === activeResultView;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-selected", String(isActive));
    button.tabIndex = isActive ? 0 : -1;
  });
  scoreFilterToolbar.hidden = activeResultView !== "ranking";
  failedActions.hidden = activeResultView !== "failed";
  errorColumnOption.hidden = activeResultView !== "failed";
  rankingTable.classList.toggle("ranking-view", activeResultView === "ranking");
  rankingTable.classList.toggle("failed-view", activeResultView === "failed");
  resultsTableWrap.classList.toggle("failed-table-wrap", activeResultView === "failed");
  resultsTableWrap.setAttribute(
    "aria-label",
    activeResultView === "ranking" ? "Successful stock score ranking" : "Failed stock responses"
  );
}

function setResultView(view) {
  if (!RESULT_VIEWS.has(view) || view === activeResultView) return;
  activeResultView = view;
  currentPage = 1;
  visibleColumns = visibleColumnsByView[view];
  saveRunViewState();
  if (currentRun) loadCurrentRun();
}

function normalizeScoreFilterValue(value) {
  if (value === "") return null;
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Math.max(0, Math.min(100, number));
}

function syncScoreFilterInputs() {
  if (scoreTargetInput) scoreTargetInput.value = scoreFilterTarget === null ? "" : String(scoreFilterTarget);
}

function uniqueScores(results) {
  if (currentRun?.result_page?.score_values) return currentRun.result_page.score_values;
  return [...new Set(
    results
      .map((result) => numericValue(result.score))
      .filter((score) => score !== null)
  )].sort((left, right) => left - right);
}

function nearestScore(results, target) {
  if (target === null) return null;
  const scores = uniqueScores(results);
  if (!scores.length) return null;
  return scores.reduce((best, score) => {
    const distance = Math.abs(score - target);
    const bestDistance = Math.abs(best - target);
    if (distance < bestDistance) return score;
    if (distance === bestDistance && score > best) return score;
    return best;
  }, scores[0]);
}

function updateMatchedScore(run) {
  matchedScore = run.result_page
    ? run.result_page.matched_score
    : nearestScore(resultsForView(run, "ranking"), scoreFilterTarget);
}

function activeScoreForStepper() {
  if (matchedScore !== null) return matchedScore;
  if (scoreFilterTarget !== null) return scoreFilterTarget;
  return null;
}

function nextScoreBucket(direction) {
  const scores = uniqueScores(resultsForView(currentRun, "ranking"));
  if (!scores.length) return null;
  const activeScore = activeScoreForStepper();
  if (activeScore === null) return direction > 0 ? scores[0] : scores[scores.length - 1];
  if (direction > 0) {
    return scores.find((score) => score > activeScore) ?? null;
  }
  for (let index = scores.length - 1; index >= 0; index -= 1) {
    if (scores[index] < activeScore) return scores[index];
  }
  return null;
}

function updateScoreStepperButtons() {
  if (!scoreDownButton || !scoreUpButton) return;
  const scores = uniqueScores(resultsForView(currentRun, "ranking"));
  const activeScore = activeScoreForStepper();
  scoreDownButton.disabled = !scores.length || (activeScore !== null && !scores.some((score) => score < activeScore));
  scoreUpButton.disabled = !scores.length || (activeScore !== null && !scores.some((score) => score > activeScore));
}

function stepScoreFilter(direction) {
  const nextScore = nextScoreBucket(direction);
  if (nextScore === null) return;
  scoreFilterTarget = nextScore;
  matchedScore = nextScore;
  currentPage = 1;
  syncScoreFilterInputs();
  saveRunViewState();
  if (currentRun) loadCurrentRun();
}

function scoreFilterLabel() {
  let scoreLabel;
  if (scoreFilterTarget === null) scoreLabel = "Showing all scores.";
  else if (matchedScore === null) scoreLabel = `No completed scores available near ${scoreFilterTarget}.`;
  else if (matchedScore === scoreFilterTarget) scoreLabel = `Showing scores equal to ${matchedScore}.`;
  else scoreLabel = `Closest score to ${scoreFilterTarget} is ${matchedScore}. Showing score ${matchedScore}.`;
  return rankingSearchQuery ? `${scoreLabel} Matching "${rankingSearchQuery}".` : scoreLabel;
}

function updateRankingSearch() {
  rankingSearchQuery = rankingSearchInput.value.trim();
  currentPage = 1;
  saveRunViewState();
  window.clearTimeout(resultFilterTimer);
  resultFilterTimer = window.setTimeout(loadCurrentRun, 250);
}

function updateScoreFilter() {
  scoreFilterTarget = normalizeScoreFilterValue(scoreTargetInput?.value ?? "");
  currentPage = 1;
  saveRunViewState();
  window.clearTimeout(resultFilterTimer);
  resultFilterTimer = window.setTimeout(loadCurrentRun, 250);
}

function clearScoreFilter() {
  scoreFilterTarget = null;
  matchedScore = null;
  rankingSearchQuery = "";
  rankingSearchInput.value = "";
  currentPage = 1;
  syncScoreFilterInputs();
  saveRunViewState();
  if (currentRun) loadCurrentRun();
}

function updateSortHeaders() {
  document.querySelectorAll("[data-sort-key]").forEach((button) => {
    const isActive = button.dataset.sortKey === sortState.key;
    const th = button.closest("th");
    if (th) {
      th.setAttribute(
        "aria-sort",
        isActive ? (sortState.direction === "asc" ? "ascending" : "descending") : "none"
      );
    }
    button.classList.toggle("is-active", isActive);
    button.dataset.direction = isActive ? sortState.direction : "";
  });
}

function setSort(key) {
  if (sortState.key === key) {
    sortState = { key, direction: sortState.direction === "asc" ? "desc" : "asc" };
  } else {
    const descendingKeys = [
      "score",
      "marketCap",
      "inputTokens",
      "responseTokens",
      "reasoningTokens",
      "durationMs",
      "cost",
    ];
    const direction = descendingKeys.includes(key)
      ? "desc"
      : "asc";
    sortState = { key, direction };
  }

  currentPage = 1;
  saveRunViewState();
  if (currentRun) loadCurrentRun();
}

async function showRunEditor() {
  if (!currentRun) {
    statusEl.textContent = "Pick a saved run before editing it.";
    return;
  }
  runEditor.hidden = false;
  runEditorName.value = currentRun.name || `Run #${currentRun.id}`;
  runEditorPrompt.value = currentRun.prompt || "";
  runEditorMaxTokens.value = String(currentRun.max_tokens || 200);
  runEditorUniverse.disabled = true;
  runEditorUniverse.innerHTML = '<option value="">Loading universes...</option>';
  runEditorUniverseHelp.textContent =
    "Only universes containing every stock already in this run can be selected.";
  try {
    const response = await fetch(`/api/runs/${currentRun.id}/universe-options`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not load stock universes");
    runEditorUniverse.innerHTML = "";
    for (const optionData of payload.options || []) {
      const option = document.createElement("option");
      option.value = optionData.stock_list_id === null ? "top" : `list:${optionData.stock_list_id}`;
      option.textContent = `${optionData.name} (${optionData.company_count})${
        optionData.archived ? " — archived" : ""
      }${!optionData.eligible ? ` — missing ${optionData.missing_count} current stocks` : ""}`;
      option.disabled = !optionData.eligible && !optionData.current;
      option.selected = Boolean(optionData.current);
      runEditorUniverse.append(option);
    }
    initialRunEditorUniverse = runEditorUniverse.value;
    runEditorUniverse.disabled = false;
  } catch (error) {
    runEditorUniverse.innerHTML = '<option value="">Unavailable</option>';
    runEditorUniverseHelp.textContent = error.message;
  }
  runEditorName.focus();
}

function hideRunEditor() {
  runEditor.hidden = true;
}

async function saveCurrentRun() {
  if (!currentRun) {
    statusEl.textContent = "Pick a saved run before editing it.";
    return;
  }

  const name = runEditorName.value.trim();
  const prompt = runEditorPrompt.value.trim();
  const maxTokens = Number(runEditorMaxTokens.value);
  const universeValue = runEditorUniverse.value;
  if (!name) {
    statusEl.textContent = "Run name is required.";
    runEditorName.focus();
    return;
  }
  if (!prompt) {
    statusEl.textContent = "Prompt is required.";
    runEditorPrompt.focus();
    return;
  }
  if (!prompt.includes("COMPANY")) {
    statusEl.textContent = "Prompt must include the COMPANY keyword.";
    runEditorPrompt.focus();
    return;
  }
  if (!Number.isInteger(maxTokens) || maxTokens < 1 || maxTokens > 32768) {
    statusEl.textContent = "Choose a response token limit from 1 to 32,768.";
    runEditorMaxTokens.focus();
    return;
  }
  if (!universeValue) {
    statusEl.textContent = "Choose an available stock universe.";
    runEditorUniverse.focus();
    return;
  }

  saveRunButton.disabled = true;
  statusEl.textContent = "Saving run...";
  try {
    const response = await fetch(`/api/runs/${currentRun.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        prompt,
        maxTokens,
        ...(universeValue !== initialRunEditorUniverse
          ? {
              stockListId:
                universeValue === "top" ? null : Number(universeValue.replace(/^list:/, "")),
            }
          : {}),
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not save run");
    await loadCurrentRun();
    hideRunEditor();
    statusEl.textContent =
      "Run changes saved. Existing results were preserved; future extensions use the selected universe.";
  } catch (error) {
    statusEl.textContent = error.message;
  } finally {
    saveRunButton.disabled = false;
  }
}

async function deleteCurrentRun() {
  if (!currentRun) {
    statusEl.textContent = "Pick a saved run before archiving.";
    return;
  }

  const label = currentRun.name || `Run #${currentRun.id}`;
  deleteButton.disabled = true;
  statusEl.textContent = `Archiving ${label}...`;
  try {
    const response = await fetch(`/api/runs/${currentRun.id}`, {
      method: "DELETE",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not archive run");
    window.location.href = "/";
  } catch (error) {
    statusEl.textContent = error.message;
    deleteButton.disabled = false;
  }
}

async function extendCurrentRun() {
  if (!currentRun) {
    statusEl.textContent = "Pick a saved run before extending.";
    return;
  }
  const extensionLimit = Number(currentRun.extension_limit || currentRun.company_count || 0);
  if (extensionLimit <= Number(currentRun.company_count || 0)) {
    statusEl.textContent = "This run already includes its entire stock universe.";
    return;
  }
  const nextCount = Math.min(extensionLimit, Number(currentRun.company_count || 0) + 10);
  const value = window.prompt(
    `Extend to how many total stocks? Current total is ${currentRun.company_count}; this universe has ${extensionLimit}.`,
    String(nextCount)
  );
  if (value === null) return;

  const companyCount = Number(value);
  if (!Number.isInteger(companyCount)) {
    statusEl.textContent = "Enter a whole number of stocks.";
    return;
  }
  if (companyCount <= Number(currentRun.company_count || 0)) {
    statusEl.textContent = `Choose a stock count above ${currentRun.company_count}.`;
    return;
  }
  if (companyCount > extensionLimit) {
    statusEl.textContent = `Choose a stock count no higher than ${extensionLimit}.`;
    return;
  }

  extendButton.disabled = true;
  try {
    const additionalCount = companyCount - Number(currentRun.company_count || 0);
    statusEl.textContent = "Estimating extension cost...";
    const confirmed = await confirmCostEstimate({
      model: currentRun.model,
      reasoningMode: currentRun.reasoning_mode,
      companyCount: additionalCount,
      actionLabel: `Extend this run by ${additionalCount} stocks?`,
    });
    if (!confirmed) {
      statusEl.textContent = "Extension canceled before any AI requests were sent.";
      return;
    }

    statusEl.textContent = `Extending to ${companyCount} stocks...`;
    const response = await fetch(`/api/runs/${currentRun.id}/extend`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ companyCount }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not extend run");
    await loadCurrentRun();
    if (pollTimer) window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(loadCurrentRun, 1000);
  } catch (error) {
    statusEl.textContent = error.message;
    extendButton.disabled = !currentRun;
  }
}

async function fillCurrentRun() {
  if (!currentRun) {
    statusEl.textContent = "Pick a saved run before filling missing scores.";
    return;
  }

  const missingCount = incompleteCount(currentRun);
  if (!missingCount) {
    statusEl.textContent = "This run already has a completed score for every selected stock.";
    return;
  }

  fillButton.disabled = true;
  try {
    statusEl.textContent = "Estimating fill cost...";
    const confirmed = await confirmCostEstimate({
      model: currentRun.model,
      reasoningMode: currentRun.reasoning_mode,
      companyCount: missingCount,
      actionLabel: `Fill ${missingCount} missing score${missingCount === 1 ? "" : "s"}?`,
    });
    if (!confirmed) {
      statusEl.textContent = "Fill canceled before any AI requests were sent.";
      return;
    }

    statusEl.textContent = `Filling ${missingCount} missing score${missingCount === 1 ? "" : "s"}...`;
    const response = await fetch(`/api/runs/${currentRun.id}/fill`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not fill missing scores");
    await loadCurrentRun();
    if (pollTimer) window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(loadCurrentRun, 1000);
  } catch (error) {
    statusEl.textContent = error.message;
    fillButton.disabled = !currentRun || canStop(currentRun) || !incompleteCount(currentRun);
  }
}

async function redriveFailedStocks() {
  if (!currentRun) {
    statusEl.textContent = "Pick a saved run before redriving failed stocks.";
    return;
  }

  const failedCount = resultsForView(currentRun, "failed").length;
  if (!failedCount) {
    statusEl.textContent = "This run has no failed stocks to redrive.";
    return;
  }

  redriveFailedButton.disabled = true;
  try {
    statusEl.textContent = "Estimating redrive cost...";
    const confirmed = await confirmCostEstimate({
      model: currentRun.model,
      reasoningMode: currentRun.reasoning_mode,
      companyCount: failedCount,
      actionLabel: `Redrive all ${failedCount} failed stock${failedCount === 1 ? "" : "s"}?`,
    });
    if (!confirmed) {
      statusEl.textContent = "Redrive canceled before any AI requests were sent.";
      return;
    }

    statusEl.textContent = `Redriving ${failedCount} failed stock${failedCount === 1 ? "" : "s"}...`;
    const response = await fetch(`/api/runs/${currentRun.id}/redrive-failed`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not redrive failed stocks");
    await loadCurrentRun();
    if (pollTimer) window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(loadCurrentRun, 1000);
  } catch (error) {
    statusEl.textContent = error.message;
  } finally {
    redriveFailedButton.disabled =
      !currentRun || canStop(currentRun) || !resultsForView(currentRun, "failed").length;
  }
}

async function redriveFailedStock(ticker, button) {
  if (!currentRun || !ticker) return;

  button.disabled = true;
  try {
    statusEl.textContent = `Estimating redrive cost for ${ticker}...`;
    const confirmed = await confirmCostEstimate({
      model: currentRun.model,
      reasoningMode: currentRun.reasoning_mode,
      companyCount: 1,
      actionLabel: `Redrive ${ticker}?`,
    });
    if (!confirmed) {
      statusEl.textContent = "Redrive canceled before any AI requests were sent.";
      return;
    }

    statusEl.textContent = `Redriving ${ticker}...`;
    const response = await fetch(
      `/api/runs/${currentRun.id}/results/${encodeURIComponent(ticker)}/redrive`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      }
    );
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `Could not redrive ${ticker}`);
    await loadCurrentRun();
    if (pollTimer) window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(loadCurrentRun, 1000);
  } catch (error) {
    statusEl.textContent = error.message;
  } finally {
    if (button.isConnected) button.disabled = !currentRun || canStop(currentRun);
  }
}

function renderRun(run) {
  currentRun = run;
  runTitle.textContent = run.name || `Run #${run.id}`;
  runPrompt.textContent = run.prompt;
  runStatus.textContent = `${run.status} ${progress(run)}`;
  const page = run.result_page || {
    page: 1,
    total_pages: 1,
    total: run.results.length,
    offset: 0,
    counts: {
      ranking: resultsForView(run, "ranking").length,
      failed: resultsForView(run, "failed").length,
    },
  };
  currentPage = page.page;
  runCount.textContent = String((page.counts?.ranking || 0) + (page.counts?.failed || 0));
  resultsPageStatus.textContent = `Page ${page.page.toLocaleString()} of ${page.total_pages.toLocaleString()}`;
  resultsPreviousButton.disabled = page.page <= 1;
  resultsNextButton.disabled = page.page >= page.total_pages;
  statusEl.textContent = run.error || "";
  renderRunStats(run);
  updateMatchedScore(run);
  updateResultViewTabs(run);
  stopButton.disabled = !canStop(run);
  stopButton.textContent = run.status === "stop_requested" ? "Stopping..." : "Stop";
  rerunButton.disabled = false;
  editRunButton.disabled = false;
  copyRunButton.disabled = false;
  extendButton.disabled = canStop(run) || Number(run.extension_limit || 0) <= Number(run.company_count || 0);
  fillButton.disabled = canStop(run) || !incompleteCount(run);
  redriveFailedButton.disabled = canStop(run) || !(page.counts?.failed || 0);
  deleteButton.disabled = false;

  if (!run.results.length) {
    const hasAnyResults = (page.counts?.ranking || 0) + (page.counts?.failed || 0) > 0;
    const emptyMessage = hasAnyResults
      ? activeResultView === "failed"
        ? "No failed responses in this run."
        : "No scores match this filter."
      : "Waiting for scores...";
    renderEmptyResults(emptyMessage);
    applyColumnVisibility();
    filterStatus.textContent = `${scoreFilterLabel()} ${page.total.toLocaleString()} rows match.`;
    updateScoreStepperButtons();
    return;
  }

  updateSortHeaders();
  const visibleResults = run.result_page ? run.results : sortedResults(
    activeResultView === "ranking" ? filteredResults(resultsForView(run)) : resultsForView(run)
  );
  filterStatus.textContent = `${scoreFilterLabel()} ${page.total.toLocaleString()} rows match. Page ${page.page.toLocaleString()} of ${page.total_pages.toLocaleString()}.`;
  updateScoreStepperButtons();
  if (!visibleResults.length) {
    const emptyMessage =
      activeResultView === "failed"
        ? "No failed responses in this run."
        : page.counts?.ranking
          ? "No scores match this filter."
          : "No successful scores yet.";
    renderEmptyResults(emptyMessage);
    applyColumnVisibility();
    restoreScrollPosition();
    return;
  }
  resultRows.innerHTML = visibleResults
    .map((result, displayIndex) => {
      const error = result.error ? escapeHtml(result.error) : "";
      const responsePageUrl = responseUrl(result);
      const detailsUrl = resultUrl(result);
      const searchUrl = `https://www.google.com/search?q=${encodeURIComponent(
        `what does ${result.company_name} (ticker: ${result.ticker}) do`
      )}`;
      const chartUrl = `https://www.google.com/search?q=${encodeURIComponent(
        `${result.ticker} stock`
      )}`;
      const dashboardUrl = `http://localhost:3000/?ticker=${encodeURIComponent(result.ticker)}`;
      const rowRedrive =
        activeResultView === "failed"
          ? `<button class="details-link row-redrive-button" type="button" data-redrive-ticker="${escapeHtml(
              result.ticker
            )}" ${canStop(run) ? "disabled" : ""}>Redrive</button>`
          : "";
      const logo = result.logo
        ? `<img class="logo" src="${escapeHtml(result.logo)}" alt="" loading="lazy" onerror="this.hidden=true" />`
        : "";
      return `
        <tr class="clickable-row" data-response-url="${responsePageUrl}">
          <td class="position-cell">${page.offset + displayIndex + 1}</td>
          <td data-column="rank">${result.scoreRank}</td>
          <td data-column="score"><strong>${formatScore(result.score)}</strong></td>
          <td data-column="scorePercentile">${formatWholePercent(result.score_percentile)}</td>
          <td data-column="confidence">${formatScore(result.confidence_score)}</td>
          <td data-column="company">
            <div class="company-cell">
              ${logo}
              <div>
                <strong class="company-name">${escapeHtml(result.company_name)}</strong>
                <span class="ticker">${escapeHtml(result.ticker)}</span>
              </div>
            </div>
          </td>
          <td data-column="marketCap">${escapeHtml(
            formatMarketCap(result.market_cap_value, result.market_cap)
          )}</td>
          <td data-column="input">${formatNumber(result.prompt_tokens)}</td>
          <td data-column="response">${formatNumber(result.response_tokens)}</td>
          <td data-column="reasoning">${formatNumber(result.reasoning_tokens)}</td>
          <td data-column="total">${formatNumber(result.total_tokens)}</td>
          <td data-column="budget">${formatPercent(result.token_budget_used_percent)}</td>
          <td data-column="time">${formatCompactSeconds(result.duration_ms)}</td>
          <td data-column="cost">${formatCompactCents(result.cost)}</td>
          <td data-column="error" class="error-cell">
            <span>${error}</span>
          </td>
          <td class="failed-redrive-column table-action-cell">
            ${rowRedrive}
          </td>
          <td data-column="search" class="search-cell">
            <button class="details-link" type="button" data-navigation-url="${escapeHtml(searchUrl)}">Search</button>
          </td>
          <td data-column="chart" class="search-cell">
            <button class="details-link" type="button" data-navigation-url="${escapeHtml(chartUrl)}">Chart</button>
          </td>
          <td data-column="dashboard" class="search-cell">
            <button class="details-link" type="button" data-navigation-url="${escapeHtml(
              dashboardUrl
            )}" title="Open stock dashboard">Dashboard</button>
          </td>
          <td data-column="actions" class="details-cell">
            <button class="details-link" type="button" data-details-url="${detailsUrl}">Details</button>
          </td>
        </tr>
      `;
    })
    .join("");
  applyColumnVisibility();
  restoreScrollPosition();
}

async function loadCurrentRun() {
  if (!currentRunId) {
    currentRun = null;
    rerunButton.disabled = true;
    editRunButton.disabled = true;
    copyRunButton.disabled = true;
    extendButton.disabled = true;
    fillButton.disabled = true;
    redriveFailedButton.disabled = true;
    deleteButton.disabled = true;
    stopButton.disabled = true;
    statusEl.textContent = "No saved runs yet.";
    renderEmptyResults("Create a scoring run first.");
    return;
  }

  const sequence = ++loadSequence;
  const query = new URLSearchParams({
    page: String(currentPage),
    pageSize: "100",
    view: activeResultView,
    sort: sortState.key,
    dir: sortState.direction,
  });
  if (scoreFilterTarget !== null) query.set("score", String(scoreFilterTarget));
  if (rankingSearchQuery) query.set("q", rankingSearchQuery);
  const response = await fetch(`/api/runs/${currentRunId}?${query.toString()}`, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Could not load run");
  if (sequence !== loadSequence) return;
  renderRun(payload.run);

  if (payload.run.status === "queued" || payload.run.status === "running" || payload.run.status === "stop_requested") {
    pollTimer = window.setTimeout(loadCurrentRun, 2500);
  }
}

function copyCurrentRun() {
  if (!currentRun) {
    statusEl.textContent = "Pick a saved run before copying its settings.";
    return;
  }
  window.location.href = `/?editRun=${encodeURIComponent(currentRun.id)}`;
}

async function stopCurrentRun() {
  if (!currentRunId || !currentRun) {
    statusEl.textContent = "Pick a running run before stopping.";
    return;
  }

  stopButton.disabled = true;
  stopButton.textContent = "Stopping...";
  statusEl.textContent = "Stop requested. The current company request may finish first.";

  try {
    const response = await fetch(`/api/runs/${currentRunId}/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not stop run");
    renderRun({ ...payload.run, results: currentRun.results });
    if (pollTimer) window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(loadCurrentRun, 1000);
  } catch (error) {
    statusEl.textContent = error.message;
    stopButton.disabled = !canStop(currentRun);
    stopButton.textContent = "Stop";
  }
}

async function rerunCurrentPrompt() {
  if (!currentRun) {
    statusEl.textContent = "Pick a saved run before rerunning.";
    return;
  }

  const name = window.prompt("Name this rerun", `${currentRun.name || `Run #${currentRun.id}`} rerun`);
  if (name === null) return;
  const trimmed = name.trim();
  if (!trimmed) {
    statusEl.textContent = "Run name is required.";
    return;
  }

  rerunButton.disabled = true;

  try {
    statusEl.textContent = "Estimating rerun cost...";
    const confirmed = await confirmCostEstimate({
      model: currentRun.model,
      reasoningMode: currentRun.reasoning_mode,
      companyCount: currentRun.company_count,
      actionLabel: "Start this rerun?",
    });
    if (!confirmed) {
      statusEl.textContent = "Rerun canceled before any AI requests were sent.";
      return;
    }

    statusEl.textContent = "Starting rerun...";
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: trimmed,
        prompt: currentRun.prompt,
        model: currentRun.model,
        reasoningMode: currentRun.reasoning_mode,
        maxTokens: currentRun.max_tokens,
        stockListId: currentRun.stock_list_id,
        ...(currentRun.stock_list_id ? { tickers: currentRun.company_tickers } : {}),
        companyCount: currentRun.company_count,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not start rerun");

    currentRunId = String(payload.runId);
    history.replaceState(null, "", `/run.html?id=${currentRunId}`);
    sortState = { key: "scoreRank", direction: "asc" };
    activeResultView = "ranking";
    restoredScroll = true;
    if (pollTimer) window.clearTimeout(pollTimer);
    await loadCurrentRun();
  } catch (error) {
    statusEl.textContent = error.message;
  } finally {
    rerunButton.disabled = !currentRun;
  }
}

stopButton.addEventListener("click", stopCurrentRun);
rerunButton.addEventListener("click", rerunCurrentPrompt);
editRunButton.addEventListener("click", showRunEditor);
copyRunButton.addEventListener("click", copyCurrentRun);
extendButton.addEventListener("click", extendCurrentRun);
fillButton.addEventListener("click", fillCurrentRun);
redriveFailedButton.addEventListener("click", redriveFailedStocks);
saveRunButton.addEventListener("click", saveCurrentRun);
cancelRunEditButton.addEventListener("click", hideRunEditor);
deleteButton.addEventListener("click", deleteCurrentRun);
scoreTargetInput.addEventListener("input", updateScoreFilter);
rankingSearchInput.addEventListener("input", updateRankingSearch);
scoreDownButton.addEventListener("click", () => stepScoreFilter(-1));
scoreUpButton.addEventListener("click", () => stepScoreFilter(1));
clearScoreFilterButton.addEventListener("click", clearScoreFilter);
resultsPreviousButton.addEventListener("click", () => {
  if (currentPage <= 1) return;
  currentPage -= 1;
  saveRunViewState();
  loadCurrentRun();
});
resultsNextButton.addEventListener("click", () => {
  const totalPages = currentRun?.result_page?.total_pages || 1;
  if (currentPage >= totalPages) return;
  currentPage += 1;
  saveRunViewState();
  loadCurrentRun();
});
document.querySelectorAll("[data-sort-key]").forEach((button) => {
  button.addEventListener("click", () => setSort(button.dataset.sortKey));
});
document.querySelectorAll("[data-result-view]").forEach((button) => {
  button.addEventListener("click", () => setResultView(button.dataset.resultView));
});
columnSelector.addEventListener("change", updateVisibleColumn);
resetColumnsButton.addEventListener("click", resetVisibleColumns);
resultRows.addEventListener("click", (event) => {
  const navigationButton = event.target.closest("[data-navigation-url]");
  if (navigationButton) {
    event.preventDefault();
    event.stopPropagation();
    saveRunViewState();
    window.location.href = navigationButton.dataset.navigationUrl;
    return;
  }
  const redriveButton = event.target.closest("[data-redrive-ticker]");
  if (redriveButton) {
    event.preventDefault();
    event.stopPropagation();
    redriveFailedStock(redriveButton.dataset.redriveTicker, redriveButton);
    return;
  }
  const row = event.target.closest("[data-response-url]");
  if (!row) return;
  const detailsLink = event.target.closest(".details-link");
  const destination = new URL(
    detailsLink?.dataset.detailsUrl || row.dataset.responseUrl,
    window.location.origin
  );
  destination.searchParams.set("sort", sortState.key);
  destination.searchParams.set("dir", sortState.direction);
  destination.searchParams.set("y", String(Math.max(0, Math.round(window.scrollY))));
  event.preventDefault();
  saveRunViewState();
  window.location.href = `${destination.pathname}${destination.search}`;
});

if ("EventSource" in window) {
  const events = new EventSource("/events");
  events.addEventListener("reload", () => window.location.reload());
}

try {
  ensureHomeLink();
  await loadPersistedVisibleColumns();
  syncScoreFilterInputs();
  rankingSearchInput.value = rankingSearchQuery;
  startEtaTimer();
  await loadCurrentRun();
} catch (error) {
  statusEl.textContent = error.message;
}
