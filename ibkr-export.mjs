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
    return { ...order, quantity: Math.floor((cents * order.weight / 100) / Math.round(price * 100) * 10000) / 10000 };
  });
}
