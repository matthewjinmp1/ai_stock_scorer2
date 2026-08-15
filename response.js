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
  responseContent: document.querySelector("#responseContent"),
  finishReason: document.querySelector("#finishReason"),
  responseError: document.querySelector("#responseError"),
  reasoningContent: document.querySelector("#reasoningContent"),
  reasoningStats: document.querySelector("#reasoningStats"),
};

const responseTabs = [...document.querySelectorAll("[data-response-view]")];
const responsePanels = [...document.querySelectorAll("[data-response-panel]")];

function text(value, fallback = "--") {
  return value === undefined || value === null || value === "" ? fallback : String(value);
}

function formatScore(value) {
  if (value === null || value === undefined) return "--";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function errorText(responseData, result) {
  const embeddedError = responseData.raw_payload?.error;
  const embeddedText =
    typeof embeddedError === "string" ? embeddedError : embeddedError?.message || "";
  const responseError = responseData.error;
  return (
    embeddedText ||
    (typeof responseError === "string" ? responseError : responseError?.message) ||
    result.error ||
    ""
  );
}

function displayReasoningTrace(value) {
  if (value === undefined || value === null || value === "") return "";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function setResponseView(view, preserveScroll = true) {
  const scrollY = window.scrollY;
  responseTabs.forEach((button) => {
    const isActive = button.dataset.responseView === view;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-selected", String(isActive));
    button.tabIndex = isActive ? 0 : -1;
  });
  responsePanels.forEach((panel) => {
    panel.hidden = panel.dataset.responsePanel !== view;
  });
  if (preserveScroll) {
    const restoreScroll = () => window.scrollTo({ top: scrollY, left: 0 });
    restoreScroll();
    queueMicrotask(restoreScroll);
    requestAnimationFrame(() => {
      restoreScroll();
      requestAnimationFrame(restoreScroll);
    });
  }
}

function setBackLink() {
  const url = new URL("/run.html", window.location.origin);
  url.searchParams.set("id", runId);
  ["sort", "dir", "tab", "score", "q", "y"].forEach((key) => {
    const value = params.get(key);
    if (value) url.searchParams.set(key, value);
  });
  els.backLink.dataset.navUrl = `${url.pathname}${url.search}`;
}

document.querySelector("[data-home-button]")?.addEventListener("click", () => {
  window.location.href = "/";
});
els.backLink.addEventListener("click", () => {
  window.location.href = els.backLink.dataset.navUrl || "/run.html";
});

async function loadResponse() {
  if (!runId || !ticker) throw new Error("Missing run or ticker.");
  setBackLink();

  const response = await fetch(
    `/api/runs/${encodeURIComponent(runId)}/results/${encodeURIComponent(ticker)}`
  );
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Could not load response");

  const { run, result, aiRequest } = payload.detail;
  const responseData = aiRequest?.response || {};
  const visibleResponse = responseData.visible_content ?? result.raw_response;
  const error = errorText(responseData, result);
  const completionDetails = aiRequest?.token_stats?.completion_tokens_details || {};
  const reasoningTokenCount = Number(completionDetails.reasoning_tokens || 0);
  const reasoningTrace = displayReasoningTrace(
    aiRequest?.chain_of_thought ||
      responseData.reasoning ||
      responseData.reasoning_content ||
      responseData.reasoning_details
  );

  document.title = `${result.company_name} AI Response`;
  els.title.textContent = result.company_name;
  els.subtitle.textContent = `${run.name || `Run #${run.id}`} • ${result.ticker}`;
  els.scoreValue.textContent = formatScore(result.score);
  els.tickerLabel.textContent = result.ticker;
  els.status.textContent = error ? "Request completed with an error." : "";
  els.responseContent.textContent = text(visibleResponse);
  els.finishReason.textContent = [
    responseData.finish_reason ? `finish: ${responseData.finish_reason}` : "",
    visibleResponse ? `${String(visibleResponse).length.toLocaleString()} chars` : "",
  ]
    .filter(Boolean)
    .join(" • ");
  els.responseError.textContent = error;
  els.reasoningStats.textContent = `${reasoningTokenCount.toLocaleString()} reasoning tokens`;
  els.reasoningContent.textContent =
    reasoningTrace ||
    aiRequest?.chain_of_thought_note ||
    "Reasoning text was not exposed by the model/API for this request.";
}

responseTabs.forEach((button) => {
  button.addEventListener("click", () => setResponseView(button.dataset.responseView));
  button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const offset = event.key === "ArrowRight" ? 1 : -1;
    const index = responseTabs.indexOf(button);
    const next = responseTabs[(index + offset + responseTabs.length) % responseTabs.length];
    setResponseView(next.dataset.responseView);
    next.focus();
  });
});

if ("EventSource" in window) {
  const events = new EventSource("/events");
  events.addEventListener("reload", () => window.location.reload());
}

try {
  await loadResponse();
} catch (error) {
  els.status.textContent = error.message;
}
