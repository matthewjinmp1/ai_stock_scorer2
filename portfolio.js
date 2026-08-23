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
const PORTFOLIO_COLUMN_KEYS = [
  "position",
  "company",
  "score",
  "scorePercentile",
  "marketCap",
  "multiplier",
  "adjustedMarketCap",
  "weight",
  "weightUplift",
];
const PORTFOLIO_COLUMN_LABELS = {
  position: "Number",
  company: "Company",
  score: "Score",
  scorePercentile: "Score Percentile",
  marketCap: "Market Cap",
  multiplier: "Score Multiplier",
  adjustedMarketCap: "Adjusted Market Cap",
  weight: "Weight",
  weightUplift: "Weight Uplift",
};

function normalizeColumnOrder(order) {
  const valid = Array.isArray(order)
    ? [...new Set(order.filter((column) => PORTFOLIO_COLUMN_KEYS.includes(column)))]
    : [];
  return [...valid, ...PORTFOLIO_COLUMN_KEYS.filter((column) => !valid.includes(column))];
}

function loadColumnOrderLocally() {
  try {
    return normalizeColumnOrder(
      JSON.parse(window.localStorage.getItem(PORTFOLIO_COLUMN_ORDER_STORAGE_KEY))
    );
  } catch (_error) {
    return [...PORTFOLIO_COLUMN_KEYS];
  }
}

function loadVisibleColumnsLocally() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(PORTFOLIO_COLUMN_STORAGE_KEY));
    if (Array.isArray(saved)) {
      const valid = saved.filter((column) => PORTFOLIO_COLUMN_KEYS.includes(column));
      if (valid.length) return new Set(valid);
    }
  } catch (_error) {
    // Use all columns when browser storage is unavailable or malformed.
  }
  return new Set(PORTFOLIO_COLUMN_KEYS);
}

let visibleColumns = loadVisibleColumnsLocally();
let columnOrder = loadColumnOrderLocally();
let columnSaveTimer = null;

function saveVisibleColumnsLocally() {
  try {
    window.localStorage.setItem(
      PORTFOLIO_COLUMN_STORAGE_KEY,
      JSON.stringify([...visibleColumns])
    );
  } catch (_error) {
    // The selection still works for this page when browser storage is unavailable.
  }
}

function saveColumnOrderLocally() {
  try {
    window.localStorage.setItem(PORTFOLIO_COLUMN_ORDER_STORAGE_KEY, JSON.stringify(columnOrder));
  } catch (_error) {
    // The order still works for this page when browser storage is unavailable.
  }
}

async function persistVisibleColumns() {
  const response = await fetch("/api/preferences/portfolio-table-columns", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ columns: [...visibleColumns], order: columnOrder }),
  });
  if (!response.ok) throw new Error("Unable to save portfolio table columns.");
}

function visibleColumnCount() {
  return visibleColumns.size;
}

function renderColumnSelectorOptions() {
  columnSelectorOptions.innerHTML = columnOrder
    .map((column, index) => {
      const label = PORTFOLIO_COLUMN_LABELS[column];
      return `<div class="column-selector-row">
        <label><input type="checkbox" data-portfolio-column-toggle="${column}" /> ${label}</label>
        <div class="column-order-controls">
          <button class="column-order-button" type="button" data-move-portfolio-column="${column}" data-move-direction="-1" title="Move earlier" aria-label="Move ${label} earlier" ${index === 0 ? "disabled" : ""}>↑</button>
          <button class="column-order-button" type="button" data-move-portfolio-column="${column}" data-move-direction="1" title="Move later" aria-label="Move ${label} later" ${index === columnOrder.length - 1 ? "disabled" : ""}>↓</button>
        </div>
      </div>`;
    })
    .join("");
}

function applyColumnOrder() {
  document.querySelectorAll(".portfolio-table tr").forEach((row) => {
    const children = [...row.children];
    columnOrder.forEach((column) => {
      const cell = children.find((candidate) => candidate.dataset.portfolioColumn === column);
      if (cell) row.append(cell);
    });
  });
}

function applyColumnVisibility() {
  applyColumnOrder();
  renderColumnSelectorOptions();
  document.querySelectorAll("[data-portfolio-column]").forEach((element) => {
    element.classList.toggle(
      "is-column-hidden",
      !visibleColumns.has(element.dataset.portfolioColumn)
    );
  });
  document.querySelectorAll("[data-portfolio-column-toggle]").forEach((input) => {
    input.checked = visibleColumns.has(input.dataset.portfolioColumnToggle);
  });
  const emptyCell = rowsEl.querySelector("td[colspan]");
  if (emptyCell) emptyCell.colSpan = visibleColumnCount();
}

function saveVisibleColumns() {
  saveVisibleColumnsLocally();
  saveColumnOrderLocally();
  if (columnSaveTimer) window.clearTimeout(columnSaveTimer);
  columnSaveTimer = window.setTimeout(() => {
    persistVisibleColumns().catch(() => {
      // Browser storage remains the fallback if the server is unavailable.
    });
  }, 200);
}

async function loadPersistedVisibleColumns() {
  try {
    const response = await fetch("/api/preferences/portfolio-table-columns");
    if (!response.ok) throw new Error("Unable to load portfolio table columns.");
    const payload = await response.json();
    if (Array.isArray(payload.columns)) {
      const valid = payload.columns.filter((column) => PORTFOLIO_COLUMN_KEYS.includes(column));
      if (valid.length) {
        visibleColumns = new Set(valid);
        saveVisibleColumnsLocally();
      }
    }
    if (Array.isArray(payload.order)) {
      columnOrder = normalizeColumnOrder(payload.order);
      saveColumnOrderLocally();
    }
    await persistVisibleColumns();
  } catch (_error) {
    // Keep the local selection when persistent preferences cannot be reached.
  }
  applyColumnVisibility();
}

function updateVisibleColumn(event) {
  const input = event.target.closest("[data-portfolio-column-toggle]");
  if (!input) return;
  const column = input.dataset.portfolioColumnToggle;
  if (input.checked) visibleColumns.add(column);
  else visibleColumns.delete(column);
  if (!visibleColumns.size) {
    visibleColumns.add(column);
    input.checked = true;
    statusEl.textContent = "Keep at least one portfolio column visible.";
    return;
  }
  saveVisibleColumns();
  applyColumnVisibility();
}

function resetVisibleColumns() {
  visibleColumns = new Set(PORTFOLIO_COLUMN_KEYS);
  saveVisibleColumns();
  applyColumnVisibility();
}

function moveColumn(column, direction) {
  const index = columnOrder.indexOf(column);
  const targetIndex = index + direction;
  if (index < 0 || targetIndex < 0 || targetIndex >= columnOrder.length) return;
  [columnOrder[index], columnOrder[targetIndex]] = [columnOrder[targetIndex], columnOrder[index]];
  saveVisibleColumns();
  applyColumnVisibility();
}

function resetColumnOrder() {
  columnOrder = [...PORTFOLIO_COLUMN_KEYS];
  saveVisibleColumns();
  applyColumnVisibility();
}

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

function ensureHomeButton() {
  document.querySelectorAll("[data-home-button]").forEach((button) => {
    button.addEventListener("click", () => {
      window.location.href = "/";
    });
  });
}

function renderPortfolio(portfolio) {
  document.title = `${portfolio.name} - Portfolio`;
  document.querySelector("#portfolioTitle").textContent = portfolio.name;
  document.querySelector("#portfolioSubtitle").textContent =
    `Top ${formatNumber(portfolio.market_cap_limit, 0)} by market cap, then scores at or above the ${formatNumber(portfolio.minimum_score_percentile)}th percentile.`;
  document.querySelector("#portfolioRun").textContent = portfolio.run_name;
  document.querySelector("#portfolioUniverse").textContent = `Top ${formatNumber(portfolio.market_cap_limit, 0)}`;
  document.querySelector("#portfolioPercentile").textContent = `${formatNumber(portfolio.minimum_score_percentile)}th`;
  document.querySelector("#portfolioMultiplier").textContent = `${formatNumber(portfolio.maximum_multiplier)}x`;
  document.querySelector("#portfolioHoldingCount").textContent = formatNumber(portfolio.holding_count, 0);
  backToRunButton.addEventListener("click", () => {
    window.location.href = `/run.html?id=${encodeURIComponent(portfolio.run_id)}`;
  });
  rowsEl.innerHTML = portfolio.holdings
    .map((holding) => {
      const logo = holding.logo
        ? `<img class="logo" src="${escapeHtml(holding.logo)}" alt="" loading="lazy" onerror="this.hidden=true" />`
        : "";
      return `
        <tr>
          <td data-label="#" data-portfolio-column="position">${formatNumber(holding.position, 0)}</td>
          <td data-label="Company" data-portfolio-column="company">
            <div class="company-cell">
              ${logo}
              <div>
                <strong class="company-name">${escapeHtml(holding.company_name)}</strong>
                <span class="ticker">${escapeHtml(holding.ticker)}</span>
              </div>
            </div>
          </td>
          <td data-label="Score" data-portfolio-column="score"><strong>${formatNumber(holding.score)}</strong></td>
          <td data-label="Score Percentile" data-portfolio-column="scorePercentile">${formatNumber(holding.score_percentile, 1)}%</td>
          <td data-label="Market Cap" data-portfolio-column="marketCap">${formatMarketCap(holding.market_cap_value)}</td>
          <td data-label="Multiplier" data-portfolio-column="multiplier">${formatNumber(holding.score_multiplier, 2)}x</td>
          <td data-label="Adjusted Market Cap" data-portfolio-column="adjustedMarketCap">${formatMarketCap(holding.adjusted_market_cap)}</td>
          <td data-label="Weight" data-portfolio-column="weight" class="portfolio-weight">${formatNumber(holding.portfolio_weight, 4)}%</td>
          <td data-label="Weight Uplift" data-portfolio-column="weightUplift" class="portfolio-weight">${formatNumber(holding.weight_uplift, 2)}x</td>
        </tr>
      `;
    })
    .join("");
  applyColumnVisibility();
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
columnSelector.addEventListener("change", updateVisibleColumn);
columnSelector.addEventListener("click", (event) => {
  const button = event.target.closest("[data-move-portfolio-column]");
  if (!button) return;
  moveColumn(button.dataset.movePortfolioColumn, Number(button.dataset.moveDirection));
});
resetColumnsButton.addEventListener("click", resetVisibleColumns);
resetColumnOrderButton.addEventListener("click", resetColumnOrder);
document.addEventListener("click", (event) => {
  if (columnSelector.open && !columnSelector.contains(event.target)) {
    columnSelector.open = false;
  }
});
try {
  await loadPersistedVisibleColumns();
  loadPortfolio();
} catch (error) {
  statusEl.textContent = error.message;
  rowsEl.innerHTML = '<tr><td colspan="9">Portfolio composition is unavailable.</td></tr>';
}
