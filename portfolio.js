import { DataTable } from "./data-table.js";
import { draftOrders, allocateOrders } from "./ibkr-export.mjs";

let exportPortfolio;
let exportOrders = [];
let savingBasket = false;
const ibkrRows = document.querySelector("#ibkrOrders");
const ibkrStatus = document.querySelector("#ibkrStatus");
const saveIbkrButton = document.querySelector("#saveIbkr");

function renderIbkrOrders() {
  ibkrRows.innerHTML = exportOrders.map((order, index) => `
    <tr data-order="${index}">
      <td><input type="checkbox" data-field="included" aria-label="Include ${escapeHtml(order.symbol)}" ${order.included ? "checked" : ""} ${!order.supported ? "disabled" : ""} />${!order.supported ? "Non-US: excluded" : ""}</td>
      <td><input data-field="symbol" aria-label="IBKR symbol for holding ${index + 1}" value="${escapeHtml(order.symbol)}" ${!order.supported ? "disabled" : ""} /></td>
      <td>${formatNumber(order.weight, 4)}%</td>
      <td><input data-field="quantity" aria-label="Shares for holding ${index + 1}" type="number" min="0" max="1000000000" step="0.0001" value="${order.quantity}" ${!order.supported ? "disabled" : ""} /></td>
      <td><input data-field="limitPrice" aria-label="Limit price for holding ${index + 1}" type="number" min="0.01" max="1000000000" step="0.01" readonly value="${escapeHtml(order.limitPrice)}" ${!order.supported ? "disabled" : ""} /></td>
    </tr>`).join("");
  updateIbkrSummary();
}

function reviewedIbkrOrders() {
  const selected = exportOrders.filter(order => order.included);
  for (const order of selected) {
    if (!Number.isFinite(Number(order.quantity)) || Math.abs(Number(order.quantity) * 10000 - Math.round(Number(order.quantity) * 10000)) > 1e-6 || Number(order.quantity) < 0 || Number(order.quantity) > 1e9) throw new Error(`${order.symbol}: enter a nonnegative share quantity with at most four decimal places.`);
  }
  return selected.filter(order => Number(order.quantity) > 0).map(order => {
    const price = Number(order.limitPrice);
    if (!Number.isFinite(price) || price < 0.01 || price > 1e9 || Math.abs(price * 100 - Math.round(price * 100)) > 1e-6) throw new Error(`${order.symbol}: fetch a valid source price before saving.`);
    return { symbol: order.symbol, sourceSymbol: order.sourceSymbol, quantity: Number(order.quantity), limitPrice: price.toFixed(2) };
  });
}

function updateIbkrSummary() {
  const summary = document.querySelector("#ibkrSummary");
  try {
    const orders = reviewedIbkrOrders();
    const total = orders.reduce((sum, order) => sum + order.quantity * Number(order.limitPrice), 0);
    const budget = Number(document.querySelector("#ibkrBudget").value);
    const remaining = budget > 0 ? ` ${total > budget ? "Over budget by" : "Unallocated budget:"} $${formatNumber(Math.abs(budget - total))}.` : "";
    const omitted = exportOrders.length - orders.length;
    summary.textContent = `${orders.length} ${orders.length === 1 ? "order" : "orders"} · $${formatNumber(total)} at limit prices, excluding fees.${remaining} ${omitted} ${omitted === 1 ? "holding" : "holdings"} omitted (excluded or zero shares).`;
    saveIbkrButton.disabled = savingBasket || !orders.length;
  } catch (error) {
    summary.textContent = error.message;
    saveIbkrButton.disabled = true;
  }
}

ibkrRows.addEventListener("input", event => {
  const field = event.target.dataset.field;
  if (!field) return;
  const order = exportOrders[Number(event.target.closest("tr").dataset.order)];
  order[field] = field === "included" ? event.target.checked : event.target.value;
  ibkrStatus.textContent = "";
  updateIbkrSummary();
});
document.querySelector("#ibkrBudget").addEventListener("input", updateIbkrSummary);
document.querySelector("#calculateIbkr").addEventListener("click", async () => {
  if (savingBasket) return;
  const button = document.querySelector("#calculateIbkr");
  button.disabled = true;
  savingBasket = true;
  exportOrders = exportOrders.map(order => ({ ...order, limitPrice: "", quantity: 0 }));
  renderIbkrOrders();
  ibkrRows.querySelectorAll("input").forEach(input => input.disabled = true);
  ibkrStatus.textContent = "Fetching US prices from CompaniesMarketCap…";
  try {
    const response = await fetch("/api/portfolios/ibkr-prices", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({symbols: exportOrders.filter(order => order.included).map(order => order.sourceSymbol)}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Price fetch failed.");
    exportOrders = exportOrders.map(order => ({...order, limitPrice: result.prices[order.sourceSymbol] || ""}));
    exportOrders = allocateOrders(exportOrders, Number(document.querySelector("#ibkrBudget").value));
    ibkrStatus.textContent = `Prices fetched ${new Date(result.fetchedAt * 1000).toLocaleString()}. Source prices may be delayed.`;
  } catch (error) { ibkrStatus.textContent = error.message; }
  finally { savingBasket = false; button.disabled = false; renderIbkrOrders(); }
});
saveIbkrButton.addEventListener("click", async () => {
  if (savingBasket) return;
  savingBasket = true;
  document.querySelector("#calculateIbkr").disabled = true;
  ibkrRows.querySelectorAll("input").forEach(input => input.disabled = true);
  saveIbkrButton.disabled = true;
  ibkrStatus.textContent = "Rechecking fresh US prices and saving CSV…";
  try {
    const response = await fetch("/api/portfolios/export-ibkr", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: exportPortfolio.name, orders: reviewedIbkrOrders() }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not save IBKR CSV.");
    ibkrStatus.textContent = `Saved ${result.orderCount} ${result.orderCount === 1 ? "order" : "orders"} to ${result.path}. In BasketTrader, click Browse, select this file, then Load.`;
  } catch (error) { ibkrStatus.textContent = error.message; }
  finally { savingBasket = false; document.querySelector("#calculateIbkr").disabled = false; renderIbkrOrders(); }
});

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
  exportPortfolio = portfolio;
  exportOrders = draftOrders(portfolio.holdings);
  renderIbkrOrders();
  document.querySelector("#ibkrExport").hidden = false;
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
