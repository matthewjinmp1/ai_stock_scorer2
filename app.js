const promptInput = document.querySelector("#promptInput");
const runNameInput = document.querySelector("#runNameInput");
const companyCountInput = document.querySelector("#companyCountInput");
const companyCountHelp = document.querySelector("#companyCountHelp");
const runButton = document.querySelector("#runButton");
const statusEl = document.querySelector("#status");
const runRows = document.querySelector("#runRows");
const countLabel = document.querySelector("#countLabel");
let companiesAvailable = 0;

function setRunRows(message) {
  runRows.innerHTML = `<tr><td colspan="8">${escapeHtml(message)}</td></tr>`;
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

async function renameRun(runId, currentName) {
  const name = window.prompt("Rename this run", currentName || "");
  if (name === null) return;
  const trimmed = name.trim();
  if (!trimmed) {
    statusEl.textContent = "Run name is required.";
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
    statusEl.textContent = `Renamed to ${payload.run.name}.`;
    await loadRuns();
  } catch (error) {
    statusEl.textContent = error.message;
  }
}

async function deleteRun(runId, currentName) {
  const label = currentName || `Run #${runId}`;
  const scrollY = window.scrollY;
  statusEl.textContent = `Archiving ${label}...`;

  try {
    const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`, {
      method: "DELETE",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not archive run");
    statusEl.textContent = `Archived ${label}.`;
    await loadRuns();
    requestAnimationFrame(() => window.scrollTo({ top: scrollY, left: 0 }));
  } catch (error) {
    statusEl.textContent = error.message;
    requestAnimationFrame(() => window.scrollTo({ top: scrollY, left: 0 }));
  }
}

async function loadCompanies() {
  const payload = await fetchJson("/api/companies");
  companiesAvailable = payload.companies.length;
  countLabel.textContent = companiesAvailable.toString();
  companyCountInput.max = String(companiesAvailable);
  companyCountHelp.textContent = `Choose 1-${companiesAvailable}. Scoring starts from the largest companies by market cap.`;
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
          <td class="prompt-cell">${escapeHtml(run.prompt)}</td>
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
  const companyCount = Number(companyCountInput.value);
  if (!Number.isInteger(companyCount) || companyCount < 1 || companyCount > companiesAvailable) {
    statusEl.textContent = `Choose a stock count from 1 to ${companiesAvailable}.`;
    companyCountInput.focus();
    return;
  }

  runButton.disabled = true;
  statusEl.textContent = `Creating scoring run for ${companyCount} stocks...`;

  try {
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, prompt, companyCount }),
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
      statusEl.textContent = error.message;
      setRunRows("Saved runs could not be loaded. Refresh the page to try again.");
    });
  }
});

if ("EventSource" in window) {
  const events = new EventSource("/events");
  events.addEventListener("reload", () => window.location.reload());
}

try {
  setRunRows("Loading saved scoring runs...");
  await loadCompanies();
  await loadRuns();
  statusEl.textContent = "Ready.";
} catch (error) {
  statusEl.textContent = error.message;
  setRunRows("Saved runs could not be loaded. Refresh the page to try again.");
}
