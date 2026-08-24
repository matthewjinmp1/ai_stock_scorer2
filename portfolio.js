import { DataTable } from "./data-table.js";

const PORTFOLIO_PREVIEW_STORAGE_KEY = "ai-stock-scorer-portfolio-preview-v1";
const statusEl = document.querySelector("#portfolioStatus");
const rowsEl = document.querySelector("#portfolioRows");
const backToRunButton = document.querySelector("#backToRunButton");
const columnSelector = document.querySelector("#portfolioColumnSelector");
const columnSelectorOptions = document.querySelector("#portfolioColumnSelectorOptions");
const resetColumnsButton = document.querySelector("#resetPortfolioColumnsButton");
const resetColumnOrderButton = document.querySelector("#resetPortfolioColumnOrderButton");
const PORTFOLIO_COLUMN_STORAGE_KEY = "ai-stock-scorer-visible-portfolio-columns-v1";
const PORTFOLIO_COLUMN_ORDER_STORAGE_KEY = "ai-stock-scorer-portfolio-column-order-v1";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatNumber(value, maximumFractionDigits = 2) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return number.toLocaleString(undefined, { maximumFractionDigits });
}

function formatMarketCap(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  if (number >= 1e12) return `$ ${formatNumber(number / 1e12)} T`;
  if (number >= 1e9) return `$ ${formatNumber(number / 1e9)} B`;
  if (number >= 1e6) return `$ ${formatNumber(number / 1e6)} M`;
  return `$ ${formatNumber(number, 0)}`;
}

const portfolioTable = new DataTable({
  table: document.querySelector(".portfolio-table"),
  body: rowsEl,
  selector: columnSelector,
  selectorOptions: columnSelectorOptions,
  resetColumnsButton,
  resetOrderButton: resetColumnOrderButton,
  storageKey: PORTFOLIO_COLUMN_STORAGE_KEY,
  orderStorageKey: PORTFOLIO_COLUMN_ORDER_STORAGE_KEY,
  statusElement: statusEl,
  loadPreferences: async () => {
    const response = await fetch("/api/preferences/portfolio-table-columns");
    if (!response.ok) throw new Error("Unable to load portfolio table columns.");
    return response.json();
  },
  savePreferences: async (_scope, preferences) => {
    const response = await fetch("/api/preferences/portfolio-table-columns", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(preferences),
    });
    if (!response.ok) throw new Error("Unable to save portfolio table columns.");
  },
  columns: [
    { key: "position", label: "Number", render: (holding) => formatNumber(holding.position, 0) },
    {
      key: "company",
      label: "Company",
      render: (holding) => {
        const logo = holding.logo
          ? `<img class="logo" src="${escapeHtml(holding.logo)}" alt="" loading="lazy" onerror="this.hidden=true" />`
          : "";
        return `<div class="company-cell">${logo}<div><strong class="company-name">${escapeHtml(
          holding.company_name
        )}</strong><span class="ticker">${escapeHtml(holding.ticker)}</span></div></div>`;
      },
    },
    { key: "score", label: "Score", render: (holding) => `<strong>${formatNumber(holding.score)}</strong>` },
    { key: "scorePercentile", label: "Score Percentile", render: (holding) => `${formatNumber(holding.score_percentile, 1)}%` },
    { key: "marketCap", label: "Market Cap", render: (holding) => formatMarketCap(holding.market_cap_value) },
    { key: "multiplier", label: "Score Multiplier", render: (holding) => `${formatNumber(holding.score_multiplier, 2)}x` },
    {
      key: "adjustedMarketCap",
      label: "Adjusted Weight Basis",
      render: (holding, _index, context) => context.baseWeighting === "equal"
        ? `${formatNumber(holding.adjusted_weighting_value ?? holding.adjusted_market_cap, 2)}x`
        : formatMarketCap(holding.adjusted_weighting_value ?? holding.adjusted_market_cap),
    },
    { key: "weight", label: "Weight", cellClass: "portfolio-weight", render: (holding) => `${formatNumber(holding.portfolio_weight, 4)}%` },
    { key: "weightUplift", label: "Weight Uplift", cellClass: "portfolio-weight", render: (holding) => `${formatNumber(holding.weight_uplift, 2)}x` },
  ],
});

function ensureHomeButton() {
  document.querySelectorAll("[data-home-button]").forEach((button) => {
    button.addEventListener("click", () => {
      window.location.href = "/";
    });
  });
}

function renderPortfolio(portfolio) {
  const baseWeighting = portfolio.base_weighting === "equal" ? "equal" : "market_cap";
  document.title = `${portfolio.name} - Portfolio`;
  document.querySelector("#portfolioTitle").textContent = portfolio.name;
  document.querySelector("#portfolioSubtitle").textContent =
    `Top ${formatNumber(portfolio.market_cap_limit, 0)} by market cap, then scores at or above the ${formatNumber(portfolio.minimum_score_percentile)}th percentile, starting from ${baseWeighting === "equal" ? "equal" : "market-cap"} weights.`;
  document.querySelector("#portfolioRun").textContent = portfolio.run_name;
  document.querySelector("#portfolioUniverse").textContent = `Top ${formatNumber(portfolio.market_cap_limit, 0)}`;
  document.querySelector("#portfolioBaseWeighting").textContent =
    baseWeighting === "equal" ? "Equal Weight" : "Market Cap";
  document.querySelector("#portfolioPercentile").textContent = `${formatNumber(portfolio.minimum_score_percentile)}th`;
  document.querySelector("#portfolioMultiplier").textContent = `${formatNumber(portfolio.maximum_multiplier)}x`;
  document.querySelector("#portfolioHoldingCount").textContent = formatNumber(portfolio.holding_count, 0);
  backToRunButton.addEventListener("click", () => {
    window.location.href = `/run.html?id=${encodeURIComponent(portfolio.run_id)}`;
  });
  portfolioTable.setContext({ baseWeighting });
  portfolioTable.setRows(portfolio.holdings, { emptyMessage: "No holdings match these portfolio rules." });
  statusEl.textContent = "";
}

function loadPortfolio() {
  const storedPortfolio = window.sessionStorage.getItem(PORTFOLIO_PREVIEW_STORAGE_KEY);
  if (!storedPortfolio) {
    throw new Error("This one-time portfolio is no longer available. Build it again from a run.");
  }
  const portfolio = JSON.parse(storedPortfolio);
  if (!portfolio || !Array.isArray(portfolio.holdings)) {
    throw new Error("This one-time portfolio is unavailable. Build it again from a run.");
  }
  renderPortfolio(portfolio);
}

ensureHomeButton();
try {
  await portfolioTable.initialize();
  loadPortfolio();
} catch (error) {
  statusEl.textContent = error.message;
  portfolioTable.setRows([], { emptyMessage: "Portfolio composition is unavailable." });
}
