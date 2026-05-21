const params = new URLSearchParams(window.location.search);
const runId = params.get("run");
const ticker = params.get("ticker");

const els = {
  title: document.querySelector("#title"),
  subtitle: document.querySelector("#subtitle"),
  scoreValue: document.querySelector("#scoreValue"),
  tickerLabel: document.querySelector("#tickerLabel"),
  backLink: document.querySelector("#backLink"),
  status: document.querySelector("#status"),
  totalTokens: document.querySelector("#totalTokens"),
  promptTokens: document.querySelector("#promptTokens"),
  completionTokens: document.querySelector("#completionTokens"),
  costValue: document.querySelector("#costValue"),
  latencyValue: document.querySelector("#latencyValue"),
  promptSent: document.querySelector("#promptSent"),
  promptLength: document.querySelector("#promptLength"),
  responseContent: document.querySelector("#responseContent"),
  finishReason: document.querySelector("#finishReason"),
  responseError: document.querySelector("#responseError"),
  promptTokenLabel: document.querySelector("#promptTokenLabel"),
  completionTokenLabel: document.querySelector("#completionTokenLabel"),
  reasoningTokenLabel: document.querySelector("#reasoningTokenLabel"),
  promptTokenBar: document.querySelector("#promptTokenBar"),
  completionTokenBar: document.querySelector("#completionTokenBar"),
  reasoningTokenBar: document.querySelector("#reasoningTokenBar"),
  runIdValue: document.querySelector("#runIdValue"),
  requestModel: document.querySelector("#requestModel"),
  responseModel: document.querySelector("#responseModel"),
  httpStatus: document.querySelector("#httpStatus"),
  reasoningSetting: document.querySelector("#reasoningSetting"),
  createdAt: document.querySelector("#createdAt"),
  reasoningTraceStats: document.querySelector("#reasoningTraceStats"),
  reasoningTrace: document.querySelector("#reasoningTrace"),
};

function text(value, fallback = "--") {
  return value === undefined || value === null || value === "" ? fallback : String(value);
}

function formatNumber(value) {
  if (value === null || value === undefined) return "--";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function formatCost(value) {
  if (value === null || value === undefined) return "--";
  const cents = Number(value) * 100;
  return `${cents.toLocaleString(undefined, {
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  })} cents`;
}

function formatMs(value) {
  if (value === null || value === undefined) return "--";
  return `${Number(value).toLocaleString()} ms`;
}

function formatDate(timestamp) {
  if (!timestamp) return "--";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(timestamp * 1000));
}

function setBar(el, value, total) {
  const width = total > 0 ? Math.max(2, Math.round((value / total) * 100)) : 0;
  el.style.width = `${width}%`;
}

function reasoningText(reasoning) {
  if (!reasoning) return "Not logged";
  const effort = reasoning.effort ? `effort: ${reasoning.effort}` : "";
  const exclude = reasoning.exclude !== undefined ? `exclude: ${reasoning.exclude}` : "";
  return [effort, exclude].filter(Boolean).join(", ") || "Configured";
}

function displayReasoningTrace(value) {
  if (value === undefined || value === null || value === "") return "";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

async function loadDetail() {
  if (!runId || !ticker) {
    throw new Error("Missing run or ticker.");
  }

  const backUrl = new URL("/run.html", window.location.origin);
  backUrl.searchParams.set("id", runId);
  ["sort", "dir", "y"].forEach((key) => {
    const value = params.get(key);
    if (value) backUrl.searchParams.set(key, value);
  });
  els.backLink.href = `${backUrl.pathname}${backUrl.search}`;

  const response = await fetch(
    `/api/runs/${encodeURIComponent(runId)}/results/${encodeURIComponent(ticker)}`
  );
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Could not load result detail");

  const { run, result, aiRequest } = payload.detail;
  const request = aiRequest?.request || {};
  const responseData = aiRequest?.response || {};
  const tokens = aiRequest?.token_stats || {};
  const completionDetails = tokens.completion_tokens_details || {};

  const prompt = request.prompt_sent || "";
  const visibleResponse = responseData.visible_content ?? result.raw_response;
  const responseText = text(visibleResponse);
  const reasoningTrace = displayReasoningTrace(
    aiRequest?.chain_of_thought ||
      responseData.reasoning ||
      responseData.reasoning_content ||
      responseData.reasoning_details
  );
  const error = responseData.error?.message || responseData.error || result.error || "";
  const total = Number(tokens.total_tokens || 0);
  const promptTokenCount = Number(tokens.prompt_tokens || 0);
  const completionTokenCount = Number(tokens.completion_tokens || 0);
  const reasoningTokenCount = Number(completionDetails.reasoning_tokens || 0);
  const responseTokenCount = Math.max(0, completionTokenCount - reasoningTokenCount);

  els.title.textContent = result.company_name;
  els.subtitle.textContent = `${run.name || `Run #${run.id}`} • ${result.ticker} • ${run.model}`;
  els.scoreValue.textContent = formatNumber(result.score);
  els.tickerLabel.textContent = result.ticker;
  els.status.textContent = error ? "Request completed with an error." : "Request completed successfully.";

  els.totalTokens.textContent = formatNumber(total);
  els.promptTokens.textContent = formatNumber(promptTokenCount);
  els.completionTokens.textContent = formatNumber(responseTokenCount);
  els.costValue.textContent = formatCost(tokens.cost);
  els.latencyValue.textContent = formatMs(aiRequest?.timing?.duration_ms);

  els.promptSent.textContent = prompt || "--";
  els.promptLength.textContent = prompt ? `${prompt.length.toLocaleString()} chars` : "";
  els.responseContent.textContent = responseText;
  els.finishReason.textContent = [
    responseData.finish_reason ? `finish: ${responseData.finish_reason}` : "",
    visibleResponse ? `${String(visibleResponse).length.toLocaleString()} chars` : "",
  ]
    .filter(Boolean)
    .join(" • ");
  els.responseError.textContent = typeof error === "string" ? error : JSON.stringify(error);

  els.promptTokenLabel.textContent = formatNumber(promptTokenCount);
  els.completionTokenLabel.textContent = formatNumber(responseTokenCount);
  els.reasoningTokenLabel.textContent = formatNumber(reasoningTokenCount);
  setBar(els.promptTokenBar, promptTokenCount, total);
  setBar(els.completionTokenBar, responseTokenCount, total);
  setBar(els.reasoningTokenBar, reasoningTokenCount, total);

  els.runIdValue.textContent = `${run.name || `Run #${run.id}`} (#${run.id})`;
  els.requestModel.textContent = text(request.model);
  els.responseModel.textContent = text(responseData.model);
  els.httpStatus.textContent = text(responseData.http_status);
  els.reasoningSetting.textContent = reasoningText(request.reasoning);
  els.createdAt.textContent = formatDate(result.created_at);
  els.reasoningTraceStats.textContent = `${formatNumber(reasoningTokenCount)} reasoning tokens`;
  els.reasoningTrace.textContent =
    reasoningTrace ||
    aiRequest?.chain_of_thought_note ||
    "Reasoning text was not exposed by the model/API for this request.";
}

if ("EventSource" in window) {
  const events = new EventSource("/events");
  events.addEventListener("reload", () => window.location.reload());
}

try {
  await loadDetail();
} catch (error) {
  els.status.textContent = error.message;
}
