const params = new URLSearchParams(window.location.search);
const runSelect = document.querySelector("#runSelect");
const stopButton = document.querySelector("#stopButton");
const rerunButton = document.querySelector("#rerunButton");
const renameButton = document.querySelector("#renameButton");
const runTitle = document.querySelector("#runTitle");
const runPrompt = document.querySelector("#runPrompt");
const runStatus = document.querySelector("#runStatus");
const runCount = document.querySelector("#runCount");
const statusEl = document.querySelector("#status");
const resultRows = document.querySelector("#resultRows");

let currentRunId = params.get("id");
let pollTimer = null;
let currentRun = null;

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

function resultUrl(result) {
  return `/result.html?run=${encodeURIComponent(currentRunId)}&ticker=${encodeURIComponent(result.ticker)}`;
}

function progress(run) {
  return `${run.completed_count + run.failed_count}/${run.company_count}`;
}

function canStop(run) {
  return run && ["queued", "running", "stop_requested"].includes(run.status);
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
    await loadRunList();
    runSelect.value = currentRunId;
    statusEl.textContent = `Renamed to ${payload.run.name}.`;
  } catch (error) {
    statusEl.textContent = error.message;
  } finally {
    renameButton.disabled = !currentRun;
  }
}

async function loadRunList() {
  const response = await fetch("/api/runs");
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Could not load runs");

  runSelect.innerHTML = "";
  for (const run of payload.runs) {
    const option = document.createElement("option");
    option.value = run.id;
    option.textContent = `${run.name || `Run #${run.id}`} - ${run.status}`;
    runSelect.append(option);
  }

  if (!currentRunId && payload.runs[0]) {
    currentRunId = String(payload.runs[0].id);
    history.replaceState(null, "", `/run.html?id=${currentRunId}`);
  }
  if (currentRunId) runSelect.value = currentRunId;
  rerunButton.disabled = !currentRunId;
  renameButton.disabled = !currentRunId;
  stopButton.disabled = true;
}

function renderRun(run) {
  currentRun = run;
  runTitle.textContent = run.name || `Run #${run.id}`;
  runPrompt.textContent = run.prompt;
  runStatus.textContent = `${run.status} ${progress(run)}`;
  runCount.textContent = String(run.results.length);
  statusEl.textContent = run.error || `Model: ${run.model}`;
  stopButton.disabled = !canStop(run);
  stopButton.textContent = run.status === "stop_requested" ? "Stopping..." : "Stop";
  rerunButton.disabled = false;
  renameButton.disabled = false;

  if (!run.results.length) {
    resultRows.innerHTML = '<tr><td colspan="7">Waiting for scores...</td></tr>';
    return;
  }

  resultRows.innerHTML = run.results
    .map((result, index) => {
      const error = result.error ? escapeHtml(result.error) : "";
      return `
        <tr>
          <td>${index + 1}</td>
          <td><strong>${formatScore(result.score)}</strong></td>
          <td>
            <div>
              <a class="company-link" href="${resultUrl(result)}">
                <strong>${escapeHtml(result.company_name)}</strong>
              </a>
              <span class="ticker">${escapeHtml(result.ticker)}</span>
            </div>
          </td>
          <td>${result.rank}</td>
          <td>${escapeHtml(result.market_cap)}</td>
          <td>${escapeHtml(result.country)}</td>
          <td class="error-cell">${error}</td>
        </tr>
      `;
    })
    .join("");
}

async function loadCurrentRun() {
  if (!currentRunId) {
    currentRun = null;
    rerunButton.disabled = true;
    renameButton.disabled = true;
    stopButton.disabled = true;
    statusEl.textContent = "No saved runs yet.";
    resultRows.innerHTML = '<tr><td colspan="7">Create a scoring run first.</td></tr>';
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
    if (pollTimer) window.clearTimeout(pollTimer);
    await loadRunList();
    await loadCurrentRun();
  } catch (error) {
    statusEl.textContent = error.message;
  } finally {
    rerunButton.disabled = !currentRun;
  }
}

runSelect.addEventListener("change", () => {
  currentRunId = runSelect.value;
  history.replaceState(null, "", `/run.html?id=${currentRunId}`);
  if (pollTimer) window.clearTimeout(pollTimer);
  loadCurrentRun();
});

stopButton.addEventListener("click", stopCurrentRun);
rerunButton.addEventListener("click", rerunCurrentPrompt);
renameButton.addEventListener("click", renameCurrentRun);

if ("EventSource" in window) {
  const events = new EventSource("/events");
  events.addEventListener("reload", () => window.location.reload());
}

try {
  ensureHomeLink();
  await loadRunList();
  await loadCurrentRun();
} catch (error) {
  statusEl.textContent = error.message;
}
