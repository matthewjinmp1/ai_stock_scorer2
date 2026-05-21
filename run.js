const params = new URLSearchParams(window.location.search);
const stopButton = document.querySelector("#stopButton");
const rerunButton = document.querySelector("#rerunButton");
const renameButton = document.querySelector("#renameButton");
const editPromptButton = document.querySelector("#editPromptButton");
const extendButton = document.querySelector("#extendButton");
const deleteButton = document.querySelector("#deleteButton");
const promptEditor = document.querySelector("#promptEditor");
const promptEditorInput = document.querySelector("#promptEditorInput");
const savePromptButton = document.querySelector("#savePromptButton");
const cancelPromptButton = document.querySelector("#cancelPromptButton");
const runTitle = document.querySelector("#runTitle");
const runPrompt = document.querySelector("#runPrompt");
const runStatus = document.querySelector("#runStatus");
const runCount = document.querySelector("#runCount");
const statusEl = document.querySelector("#status");
const resultRows = document.querySelector("#resultRows");
const statModel = document.querySelector("#statModel");
const statProgress = document.querySelector("#statProgress");
const statCost = document.querySelector("#statCost");
const statTokens = document.querySelector("#statTokens");
const statLatency = document.querySelector("#statLatency");
const statScoreRange = document.querySelector("#statScoreRange");
const statAverageScore = document.querySelector("#statAverageScore");
const statRequests = document.querySelector("#statRequests");

const SORT_KEYS = new Set([
  "scoreRank",
  "score",
  "company",
  "inputTokens",
  "responseTokens",
  "reasoningTokens",
  "cost",
  "error",
]);
const SORT_DIRECTIONS = new Set(["asc", "desc"]);

let currentRunId = params.get("id");
let pollTimer = null;
let currentRun = null;
const requestedSortKey = ["totalTokens", "outputTokens"].includes(params.get("sort"))
  ? "responseTokens"
  : params.get("sort");
let sortState = {
  key: SORT_KEYS.has(requestedSortKey) ? requestedSortKey : "scoreRank",
  direction: SORT_DIRECTIONS.has(params.get("dir")) ? params.get("dir") : "asc",
};
let restoredScroll = false;

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

  const existingHome = document.querySelector('a[href="/"]');
  if (existingHome) {
    existingHome.textContent = "Home";
    existingHome.classList.add("secondary-link");
    existingHome.classList.add("nav-link");
    if (existingHome.parentElement !== nav) nav.append(existingHome);
    return;
  }

  const homeLink = document.createElement("a");
  homeLink.href = "/";
  homeLink.className = "secondary-link nav-link";
  homeLink.textContent = "Home";
  nav.append(homeLink);
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
  if (!cents) return "0.0000 cents";
  return `${cents.toLocaleString(undefined, {
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  })} cents`;
}

function formatNumber(value) {
  if (value === null || value === undefined) return "--";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function formatMs(value) {
  if (value === null || value === undefined) return "--";
  return `${Number(value).toLocaleString()} ms`;
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
  url.searchParams.set("y", String(Math.max(0, Math.round(scrollY))));
  return `${url.pathname}${url.search}`;
}

function resultUrl(result, scrollY = window.scrollY) {
  const url = new URL("/result.html", window.location.origin);
  url.searchParams.set("run", currentRunId);
  url.searchParams.set("ticker", result.ticker);
  url.searchParams.set("sort", sortState.key);
  url.searchParams.set("dir", sortState.direction);
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
  const scores = numericScores(run);
  const minScore = scores.length ? Math.min(...scores) : null;
  const maxScore = scores.length ? Math.max(...scores) : null;
  const averageScore = scores.length
    ? scores.reduce((sum, score) => sum + score, 0) / scores.length
    : null;

  statModel.innerHTML = `
    <span class="model-label">${escapeHtml(model.label || run.model)}</span>
    <span class="model-id">${escapeHtml(model.id || run.model)}</span>
    <span class="model-id">reasoning: ${escapeHtml(reasoning.effort || "none")}, exclude: ${escapeHtml(
    reasoning.exclude === undefined ? "true" : String(reasoning.exclude)
  )}</span>
  `;
  statProgress.textContent = progress(run);
  statCost.textContent = formatCents(stats.cost);
  statTokens.textContent = formatNumber(stats.total_tokens);
  statLatency.textContent = formatMs(stats.average_latency_ms);
  statScoreRange.textContent =
    minScore === null ? "--" : `${formatScore(minScore)}-${formatScore(maxScore)}`;
  statAverageScore.textContent = averageScore === null ? "--" : formatScore(averageScore);
  statRequests.textContent = `${formatNumber(stats.successful_request_count || 0)} ok / ${formatNumber(
    stats.failed_request_count || 0
  )} failed`;
}

function sortValue(result, key) {
  if (key === "scoreRank") return result.scoreRank;
  if (key === "score") return numericValue(result.score);
  if (key === "company") return `${result.company_name || ""} ${result.ticker || ""}`.toLowerCase();
  if (key === "inputTokens") return numericValue(result.prompt_tokens);
  if (key === "responseTokens") return numericValue(result.response_tokens);
  if (key === "reasoningTokens") return numericValue(result.reasoning_tokens);
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
    const direction = ["score", "inputTokens", "responseTokens", "reasoningTokens", "cost"].includes(key)
      ? "desc"
      : "asc";
    sortState = { key, direction };
  }

  saveRunViewState();
  if (currentRun) renderRun(currentRun);
}

async function renameCurrentRun() {
  if (!currentRun) {
    statusEl.textContent = "Pick a saved run before renaming.";
    return;
  }

  const name = window.prompt("Rename this run", currentRun.name || `Run #${currentRun.id}`);
  if (name === null) return;
  const trimmed = name.trim();
  if (!trimmed) {
    statusEl.textContent = "Run name is required.";
    return;
  }

  renameButton.disabled = true;
  try {
    const response = await fetch(`/api/runs/${currentRun.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: trimmed }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not rename run");
    renderRun(payload.run);
    statusEl.textContent = `Renamed to ${payload.run.name}.`;
  } catch (error) {
    statusEl.textContent = error.message;
  } finally {
    renameButton.disabled = !currentRun;
  }
}

function showPromptEditor() {
  if (!currentRun) {
    statusEl.textContent = "Pick a saved run before editing its prompt.";
    return;
  }
  promptEditor.hidden = false;
  promptEditorInput.value = currentRun.prompt || "";
  promptEditorInput.focus();
}

function hidePromptEditor() {
  promptEditor.hidden = true;
}

async function saveCurrentPrompt() {
  if (!currentRun) {
    statusEl.textContent = "Pick a saved run before editing its prompt.";
    return;
  }

  const prompt = promptEditorInput.value.trim();
  if (!prompt) {
    statusEl.textContent = "Prompt is required.";
    promptEditorInput.focus();
    return;
  }
  if (!prompt.includes("COMPANY")) {
    statusEl.textContent = "Prompt must include the COMPANY keyword.";
    promptEditorInput.focus();
    return;
  }

  savePromptButton.disabled = true;
  statusEl.textContent = "Saving prompt...";
  try {
    const response = await fetch(`/api/runs/${currentRun.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not save prompt");
    renderRun(payload.run);
    hidePromptEditor();
    statusEl.textContent = "Prompt saved. Rerun will use the edited prompt.";
  } catch (error) {
    statusEl.textContent = error.message;
  } finally {
    savePromptButton.disabled = false;
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

  const nextCount = Math.min(100, Number(currentRun.company_count || 0) + 10);
  const value = window.prompt(
    `Extend to how many total stocks? Current total is ${currentRun.company_count}.`,
    String(nextCount)
  );
  if (value === null) return;

  const companyCount = Number(value);
  if (!Number.isInteger(companyCount)) {
    statusEl.textContent = "Enter a whole number of stocks.";
    return;
  }

  extendButton.disabled = true;
  statusEl.textContent = `Extending to ${companyCount} stocks...`;
  try {
    const response = await fetch(`/api/runs/${currentRun.id}/extend`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ companyCount }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not extend run");
    renderRun(payload.run);
    if (pollTimer) window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(loadCurrentRun, 1000);
  } catch (error) {
    statusEl.textContent = error.message;
    extendButton.disabled = !currentRun;
  }
}

function renderRun(run) {
  currentRun = run;
  runTitle.textContent = run.name || `Run #${run.id}`;
  runPrompt.textContent = run.prompt;
  runStatus.textContent = `${run.status} ${progress(run)}`;
  runCount.textContent = String(run.results.length);
  statusEl.textContent = run.error || "";
  renderRunStats(run);
  stopButton.disabled = !canStop(run);
  stopButton.textContent = run.status === "stop_requested" ? "Stopping..." : "Stop";
  rerunButton.disabled = false;
  renameButton.disabled = false;
  editPromptButton.disabled = false;
  extendButton.disabled = canStop(run);
  deleteButton.disabled = false;

  if (!run.results.length) {
    resultRows.innerHTML = '<tr><td colspan="8">Waiting for scores...</td></tr>';
    return;
  }

  updateSortHeaders();
  resultRows.innerHTML = sortedResults(run.results)
    .map((result) => {
      const error = result.error ? escapeHtml(result.error) : "";
      const url = resultUrl(result);
      return `
        <tr class="clickable-row" data-result-url="${url}">
          <td>${result.scoreRank}</td>
          <td><strong>${formatScore(result.score)}</strong></td>
          <td>
            <div>
              <a class="company-link" href="${url}">
                <strong>${escapeHtml(result.company_name)}</strong>
              </a>
              <span class="ticker">${escapeHtml(result.ticker)}</span>
            </div>
          </td>
          <td>${formatNumber(result.prompt_tokens)}</td>
          <td>${formatNumber(result.response_tokens)}</td>
          <td>${formatNumber(result.reasoning_tokens)}</td>
          <td>${formatCents(result.cost)}</td>
          <td class="error-cell">${error}</td>
        </tr>
      `;
    })
    .join("");
  restoreScrollPosition();
}

async function loadCurrentRun() {
  if (!currentRunId) {
    currentRun = null;
    rerunButton.disabled = true;
    renameButton.disabled = true;
    editPromptButton.disabled = true;
    extendButton.disabled = true;
    deleteButton.disabled = true;
    stopButton.disabled = true;
    statusEl.textContent = "No saved runs yet.";
    resultRows.innerHTML = '<tr><td colspan="8">Create a scoring run first.</td></tr>';
    return;
  }

  const response = await fetch(`/api/runs/${currentRunId}`);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Could not load run");
  renderRun(payload.run);

  if (payload.run.status === "queued" || payload.run.status === "running" || payload.run.status === "stop_requested") {
    pollTimer = window.setTimeout(loadCurrentRun, 2500);
  }
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
  statusEl.textContent = "Starting rerun...";

  try {
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: trimmed,
        prompt: currentRun.prompt,
        companyCount: currentRun.company_count,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not start rerun");

    currentRunId = String(payload.runId);
    history.replaceState(null, "", `/run.html?id=${currentRunId}`);
    sortState = { key: "scoreRank", direction: "asc" };
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
renameButton.addEventListener("click", renameCurrentRun);
editPromptButton.addEventListener("click", showPromptEditor);
extendButton.addEventListener("click", extendCurrentRun);
savePromptButton.addEventListener("click", saveCurrentPrompt);
cancelPromptButton.addEventListener("click", hidePromptEditor);
deleteButton.addEventListener("click", deleteCurrentRun);
document.querySelectorAll("[data-sort-key]").forEach((button) => {
  button.addEventListener("click", () => setSort(button.dataset.sortKey));
});
resultRows.addEventListener("click", (event) => {
  const row = event.target.closest("[data-result-url]");
  if (!row) return;
  const link = row.querySelector(".company-link");
  const destination = new URL(link?.getAttribute("href") || row.dataset.resultUrl, window.location.origin);
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
  await loadCurrentRun();
} catch (error) {
  statusEl.textContent = error.message;
}
