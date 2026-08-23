const PORTFOLIO_PREVIEW_STORAGE_KEY = "ai-stock-scorer-portfolio-preview-v1";
const statusEl = document.querySelector("#portfolioStatus");
const rowsEl = document.querySelector("#portfolioRows");
const backToRunButton = document.querySelector("#backToRunButton");

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
          <td data-label="#">${formatNumber(holding.position, 0)}</td>
          <td data-label="Company">
            <div class="company-cell">
              ${logo}
              <div>
                <strong class="company-name">${escapeHtml(holding.company_name)}</strong>
                <span class="ticker">${escapeHtml(holding.ticker)}</span>
              </div>
            </div>
          </td>
          <td data-label="Score"><strong>${formatNumber(holding.score)}</strong></td>
          <td data-label="Score Percentile">${formatNumber(holding.score_percentile, 1)}%</td>
          <td data-label="Market Cap">${formatMarketCap(holding.market_cap_value)}</td>
          <td data-label="Multiplier">${formatNumber(holding.score_multiplier, 2)}x</td>
          <td data-label="Adjusted Market Cap">${formatMarketCap(holding.adjusted_market_cap)}</td>
          <td data-label="Weight" class="portfolio-weight">${formatNumber(holding.portfolio_weight, 4)}%</td>
          <td data-label="Weight Uplift" class="portfolio-weight">${formatNumber(holding.weight_uplift, 2)}x</td>
        </tr>
      `;
    })
    .join("");
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
  loadPortfolio();
} catch (error) {
  statusEl.textContent = error.message;
  rowsEl.innerHTML = '<tr><td colspan="9">Portfolio composition is unavailable.</td></tr>';
}
