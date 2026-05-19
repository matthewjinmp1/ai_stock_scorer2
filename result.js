const params = new URLSearchParams(window.location.search);
const runId = params.get("run");
const ticker = params.get("ticker");

const titleEl = document.querySelector("#title");
const subtitleEl = document.querySelector("#subtitle");
const scoreValue = document.querySelector("#scoreValue");
const tickerLabel = document.querySelector("#tickerLabel");
const backLink = document.querySelector("#backLink");
const statusEl = document.querySelector("#status");
const promptSent = document.querySelector("#promptSent");
const aiResponse = document.querySelector("#aiResponse");
const tokenStats = document.querySelector("#tokenStats");
const metadata = document.querySelector("#metadata");

function pretty(value) {
  if (value === undefined || value === null || value === "") return "--";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function formatScore(score) {
  if (score === null || score === undefined) return "--";
  return Number(score).toLocaleString(undefined, { maximumFractionDigits: 4 });
}

async function loadDetail() {
  if (!runId || !ticker) {
    throw new Error("Missing run or ticker.");
  }

  backLink.href = `/run.html?id=${encodeURIComponent(runId)}`;

  const response = await fetch(
    `/api/runs/${encodeURIComponent(runId)}/results/${encodeURIComponent(ticker)}`
  );
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Could not load result detail");

  const { run, result, aiRequest } = payload.detail;
  const request = aiRequest?.request;
  const aiResponseData = aiRequest?.response;

  titleEl.textContent = result.company_name;
  subtitleEl.textContent = `Run #${run.id} • ${result.ticker} • ${run.model}`;
  scoreValue.textContent = formatScore(result.score);
  tickerLabel.textContent = result.ticker;
  statusEl.textContent = result.error || run.error || "Loaded request detail.";

  promptSent.textContent = pretty(request?.prompt_sent);
  aiResponse.textContent = pretty({
    visible_content: aiResponseData?.visible_content ?? result.raw_response,
    finish_reason: aiResponseData?.finish_reason,
    error: aiResponseData?.error ?? result.error,
  });
  tokenStats.textContent = pretty(aiRequest?.token_stats);
  metadata.textContent = pretty({
    run_id: run.id,
    ticker: result.ticker,
    company_name: result.company_name,
    score: result.score,
    request_model: request?.model,
    response_model: aiResponseData?.model,
    http_status: aiResponseData?.http_status,
    timing: aiRequest?.timing,
    reasoning: request?.reasoning,
    created_at: result.created_at,
  });
}

if ("EventSource" in window) {
  const events = new EventSource("/events");
  events.addEventListener("reload", () => window.location.reload());
}

try {
  await loadDetail();
} catch (error) {
  statusEl.textContent = error.message;
}
