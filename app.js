const promptInput = document.querySelector("#promptInput");
const companyCountInput = document.querySelector("#companyCountInput");
const companyCountHelp = document.querySelector("#companyCountHelp");
const runButton = document.querySelector("#runButton");
const statusEl = document.querySelector("#status");
const runRows = document.querySelector("#runRows");
const countLabel = document.querySelector("#countLabel");
let companiesAvailable = 0;

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

async function loadCompanies() {
  const response = await fetch("/api/companies");
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Could not load companies");
  companiesAvailable = payload.companies.length;
  countLabel.textContent = companiesAvailable.toString();
  companyCountInput.max = String(companiesAvailable);
  companyCountHelp.textContent = `Choose 1-${companiesAvailable}. Scoring starts from the largest companies by market cap.`;
}

async function loadRuns() {
  const response = await fetch("/api/runs");
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Could not load runs");

  if (!payload.runs.length) {
    runRows.innerHTML = '<tr><td colspan="6">No saved scoring runs yet.</td></tr>';
    return;
  }

  runRows.innerHTML = payload.runs
    .map(
      (run) => `
        <tr>
          <td>#${run.id}</td>
          <td class="prompt-cell">${escapeHtml(run.prompt)}</td>
          <td><span class="${statusClass(run.status)}">${escapeHtml(run.status)}</span></td>
          <td>${progress(run)}</td>
          <td>${formatDate(run.created_at)}</td>
          <td><a class="row-link" href="/run.html?id=${run.id}">Open</a></td>
        </tr>
      `
    )
    .join("");
}

async function createRun() {
  const prompt = promptInput.value.trim();
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
      body: JSON.stringify({ prompt, companyCount }),
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

if ("EventSource" in window) {
  const events = new EventSource("/events");
  events.addEventListener("reload", () => window.location.reload());
}

try {
  await loadCompanies();
  await loadRuns();
  statusEl.textContent = "Ready.";
} catch (error) {
  statusEl.textContent = error.message;
}
