import { DataTable } from "./data-table.js";
import { draftOrders, allocateOrders, allocateBuysOverPositions } from "./ibkr-export.mjs";

let exportPortfolio;
let exportOrders = [];
let savingBasket = false;
const ibkrRows = document.querySelector("#ibkrOrders");
const ibkrStatus = document.querySelector("#ibkrStatus");
const saveIbkrButton = document.querySelector("#saveIbkr");

function showPortfolioView() {
  const exporting = Boolean(exportPortfolio) && window.location.hash === "#ibkr";
  document.querySelectorAll("[data-portfolio-view]").forEach(element => { element.hidden = exporting; });
  document.querySelector("#ibkrExport").hidden = !exporting;
  if (exportPortfolio) document.title = `${exportPortfolio.name} - ${exporting ? "Export to IBKR" : "Portfolio"}`;
}

document.querySelector("#openIbkrExport").addEventListener("click", () => {
  window.location.hash = "ibkr";
});
document.querySelector("#backToPortfolio").addEventListener("click", () => {
  window.location.hash = "";
});
window.addEventListener("hashchange", () => {
  showPortfolioView();
  const target = window.location.hash === "#ibkr" ? "#ibkrExportTitle" : "#openIbkrExport";
  document.querySelector(target).focus();
  window.scrollTo({ top: 0 });
});

function renderIbkrOrders() {
  ibkrRows.innerHTML = exportOrders.map((order, index) => {
    const holding = exportPortfolio.holdings[index];
    const logo = holding.logo ? `<img class="logo" src="${escapeHtml(holding.logo)}" alt="" loading="lazy" onerror="this.hidden=true" />` : "";
    const price = Number(order.limitPrice);
    const priced = order.limitPrice !== "" && Number.isFinite(price) && price > 0;
    const current = order.currentQuantity;
    const buy = order.included ? Number(order.quantity) : 0;
    const currentCents = current == null || !priced ? null : Math.round(current * price * 100);
    const buyCents = priced ? Math.round(buy * price * 100) : null;
    const positionCell = (shares, cents) => shares == null ? "—" : `<strong>${cents == null ? "—" : (cents / 100).toLocaleString("en-US", {style: "currency", currency: "USD"})}</strong><span class="ticker">${formatNumber(shares, 4)} shares</span>`;
    return `<tr data-order="${index}">
      <td>${index + 1}</td>
      <td><div class="company-cell">${logo}<div><strong class="company-name">${escapeHtml(holding.company_name || holding.ticker)}</strong><span class="ticker">${escapeHtml(order.symbol)}</span>${!order.supported ? '<span class="ticker">Non-US: excluded</span>' : ""}</div></div></td>
      <td><strong>${formatNumber(holding.score)}</strong></td>
      <td>${formatNumber(holding.score_percentile, 1)}%</td>
      <td>${formatNumber(order.weight, 4)}%</td>
      <td>${positionCell(current, currentCents)}</td>
      <td>${positionCell(buy, buyCents)}</td>
      <td>${positionCell(current == null ? null : current + buy, currentCents == null || buyCents == null ? null : currentCents + buyCents)}</td>
      <td>${priced ? price.toLocaleString("en-US", {style: "currency", currency: "USD"}) : "—"}</td>
    </tr>`;
  }).join("");
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

document.querySelector("#allCashIbkr").addEventListener("click", async () => {
  if (savingBasket) return;
  savingBasket = true;
  const button = document.querySelector("#allCashIbkr");
  button.disabled = true;
  document.querySelector("#calculateIbkr").disabled = true;
  updateIbkrSummary();
  ibkrStatus.textContent = "Reading available cash from TWS…";
  try {
    const response = await fetch("/api/portfolios/ibkr-cash", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Unable to read cash.");
    document.querySelector("#ibkrBudget").value = result.cash;
    exportOrders = exportOrders.map(order => ({...order, quantity: 0}));
    ibkrStatus.textContent = `Budget set to $${result.cash}. Fetch prices and calculate shares next. Fees are not reserved.`;
  } catch (error) { ibkrStatus.textContent = error.message; }
  finally {
    savingBasket = false;
    button.disabled = false;
    document.querySelector("#calculateIbkr").disabled = false;
    renderIbkrOrders();
  }
});
function invalidateIbkrSizing() {
  exportOrders = exportOrders.map(order => ({...order, quantity: 0, currentQuantity: undefined}));
  renderIbkrOrders();
}
document.querySelector("#ibkrBudget").addEventListener("input", invalidateIbkrSizing);
document.querySelector("#balanceIbkr").addEventListener("change", invalidateIbkrSizing);
document.querySelector("#calculateIbkr").addEventListener("click", async () => {
  if (savingBasket) return;
  const button = document.querySelector("#calculateIbkr");
  button.disabled = true;
  const balance = document.querySelector("#balanceIbkr").checked;
  const budget = Number(document.querySelector("#ibkrBudget").value);
  document.querySelector("#balanceIbkr").disabled = true;
  document.querySelector("#ibkrBudget").disabled = true;
  savingBasket = true;
  exportOrders = exportOrders.map(order => ({ ...order, limitPrice: "", quantity: 0, currentQuantity: undefined }));
  renderIbkrOrders();
  ibkrStatus.textContent = "Fetching US prices from CompaniesMarketCap…";
  try {
    const response = await fetch("/api/portfolios/ibkr-prices", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({symbols: exportOrders.filter(order => order.included).map(order => order.sourceSymbol)}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Price fetch failed.");
    exportOrders = exportOrders.map(order => ({...order, limitPrice: result.prices[order.sourceSymbol] || ""}));
    if (balance) {
      ibkrStatus.textContent = "Reading current positions from TWS…";
      const positionsResponse = await fetch("/api/portfolios/ibkr-positions", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
      const snapshot = await positionsResponse.json();
      if (!positionsResponse.ok) throw new Error(snapshot.error || "Unable to read positions.");
      exportOrders = allocateBuysOverPositions(exportOrders, budget, snapshot.positions);
    } else {
      exportOrders = allocateOrders(exportOrders, budget);
    }
    ibkrStatus.textContent = `Prices fetched ${new Date(result.fetchedAt * 1000).toLocaleString()}. Source prices may be delayed.`;
  } catch (error) { ibkrStatus.textContent = error.message; }
  finally { savingBasket = false; button.disabled = false; document.querySelector("#balanceIbkr").disabled = false; document.querySelector("#ibkrBudget").disabled = false; renderIbkrOrders(); }
});
saveIbkrButton.addEventListener("click", async () => {
  if (savingBasket) return;
  savingBasket = true;
  document.querySelector("#calculateIbkr").disabled = true;
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
  document.querySelector("#openIbkrExport").disabled = false;
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
  showPortfolioView();
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
