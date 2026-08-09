const params = new URLSearchParams(window.location.search);
const runId = params.get("run");
const ticker = params.get("ticker");

const els = {
  title: document.querySelector("#title"),
  subtitle: document.querySelector("#subtitle"),
  reasoningTokens: document.querySelector("#reasoningTokens"),
  reasoningStats: document.querySelector("#reasoningStats"),
  reasoningContent: document.querySelector("#reasoningContent"),
  responseLink: document.querySelector("#responseLink"),
  backLink: document.querySelector("#backLink"),
  status: document.querySelector("#status"),
};

function formatNumber(value) {
  return Number(value || 0).toLocaleString();
}

function displayReasoningTrace(value) {
  if (value === undefined || value === null || value === "") return "";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function setLinks() {
  const responseUrl = new URL("/response.html", window.location.origin);
  params.forEach((value, key) => responseUrl.searchParams.set(key, value));
  els.responseLink.href = `${responseUrl.pathname}${responseUrl.search}`;

  const runUrl = new URL("/run.html", window.location.origin);
  runUrl.searchParams.set("id", runId);
  ["sort", "dir", "tab", "score", "q", "y"].forEach((key) => {
    const value = params.get(key);
    if (value) runUrl.searchParams.set(key, value);
  });
  els.backLink.href = `${runUrl.pathname}${runUrl.search}`;
}

async function loadReasoning() {
  if (!runId || !ticker) throw new Error("Missing run or ticker.");
  setLinks();

  const response = await fetch(
    `/api/runs/${encodeURIComponent(runId)}/results/${encodeURIComponent(ticker)}`
  );
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Could not load reasoning trace");

  const { run, result, aiRequest } = payload.detail;
  const responseData = aiRequest?.response || {};
  const completionDetails = aiRequest?.token_stats?.completion_tokens_details || {};
  const reasoningTokenCount = Number(completionDetails.reasoning_tokens || 0);
  const trace = displayReasoningTrace(
    aiRequest?.chain_of_thought ||
      responseData.reasoning ||
      responseData.reasoning_content ||
      responseData.reasoning_details
  );

  document.title = `${result.company_name} Reasoning Trace`;
  els.title.textContent = result.company_name;
  els.subtitle.textContent = `${run.name || `Run #${run.id}`} • ${result.ticker}`;
  els.reasoningTokens.textContent = formatNumber(reasoningTokenCount);
  els.reasoningStats.textContent = trace
    ? `${trace.length.toLocaleString()} chars`
    : "Not exposed by model";
  els.reasoningContent.textContent =
    trace ||
    aiRequest?.chain_of_thought_note ||
    "Reasoning text was not exposed by the model/API for this request.";
}

if ("EventSource" in window) {
  const events = new EventSource("/events");
  events.addEventListener("reload", () => window.location.reload());
}

try {
  await loadReasoning();
} catch (error) {
  els.status.textContent = error.message;
}
