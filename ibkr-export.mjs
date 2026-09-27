export function draftOrders(holdings) {
  return holdings.map((holding) => {
    const supported = ["USA", "United States"].includes(holding.country);
    return {
      symbol: /^BRK[-.][AB]$/.test(holding.ticker) ? holding.ticker.replace(/[-.]/g, " ") : holding.ticker.replaceAll("-", " "),
      sourceSymbol: holding.ticker,
      weight: Number(holding.portfolio_weight),
      supported,
      included: supported,
      quantity: 0,
      limitPrice: "",
    };
  });
}

// Limits sit above the fetched price so scheduled orders fill like market orders. IBKR Lite
// treats market orders placed before the open as OnOpen orders, which lose commission-free pricing.
export const LIMIT_BUFFER_PERCENT = 1;

export function limitPriceFor(price) {
  return Math.ceil(Math.round(Number(price) * 100) * (100 + LIMIT_BUFFER_PERCENT) / 100) / 100;
}

export function allocateOrders(orders, budget) {
  if (!Number.isFinite(budget) || budget <= 0 || budget > 1e12) throw new Error("Enter a positive USD budget up to 1 trillion.");
  const cents = Math.round(budget * 100);
  return orders.map((order) => {
    if (!order.included) return { ...order, quantity: 0 };
    const price = Number(order.limitPrice);
    if (!Number.isFinite(price) || price < 0.01 || price > 1e9 || Math.abs(price * 100 - Math.round(price * 100)) > 1e-6) {
      throw new Error(`${order.symbol}: enter a positive limit price with at most two decimal places.`);
    }
    if (!Number.isFinite(order.weight) || order.weight <= 0 || order.weight > 100) throw new Error(`${order.symbol}: invalid portfolio weight. Rebuild the portfolio.`);
    return { ...order, quantity: Math.floor((cents * order.weight / 100) / Math.round(limitPriceFor(price) * 100) * 10000) / 10000 };
  });
}

export function allocateBuysOverPositions(orders, budget, positions) {
  // Validate prices and weights through the same rules as ordinary purchases.
  allocateOrders(orders, budget);
  if (!Array.isArray(positions)) throw new Error("Complete positions are required.");
  const normalize = symbol => symbol.replace(/[ .-]/g, " ");
  const holdings = new Map();
  for (const position of positions) {
    if (position.type !== "STK" || position.currency !== "USD") continue;
    const quantity = Number(position.quantity);
    if (!Number.isFinite(quantity)) throw new Error("Invalid position quantity from TWS.");
    const key = normalize(position.symbol);
    holdings.set(key, (holdings.get(key) || 0) + quantity);
  }
  const rows = orders.map(order => ({...order, currentQuantity: holdings.get(normalize(order.symbol)) || 0}));
  const included = rows.filter(order => order.included);
  if (included.some(order => order.currentQuantity < 0)) throw new Error("Buy-only balancing does not support short positions in target stocks.");
  const weightSum = included.reduce((sum, order) => sum + order.weight, 0);
  if (!weightSum) throw new Error("No eligible target stocks.");
  const cash = Math.floor(budget * 100) / 100;
  // Raise underweight holdings toward a common target level. Overweight holdings stay untouched.
  // Positions are valued at fetched prices; purchases must fit the budget at limit prices.
  const sharesAt = (order, level) => Math.max(0, level * order.weight / weightSum / Number(order.limitPrice) - order.currentQuantity);
  let low = 0;
  let high = cash + included.reduce((sum, order) => sum + order.currentQuantity * Number(order.limitPrice), 0);
  for (let i = 0; i < 80; i++) {
    const level = (low + high) / 2;
    const cost = included.reduce((sum, order) => sum + sharesAt(order, level) * limitPriceFor(order.limitPrice), 0);
    if (cost > cash) high = level; else low = level;
  }
  return rows.map(order => ({...order, quantity: order.included ? Math.floor(sharesAt(order, low) * 10000) / 10000 : 0}));
}
