import test from 'node:test';
import assert from 'node:assert/strict';
import { draftOrders, allocateOrders } from './ibkr-export.mjs';

test('budget sizing rounds down and leaves excluded weights unallocated', () => {
  const drafts = draftOrders([
    {ticker:'AAA', country:'USA', price:'$300', portfolio_weight:60},
    {ticker:'BBB', country:'USA', price:'$200', portfolio_weight:30},
    {ticker:'CCC', country:'UK', price:'$100', portfolio_weight:10},
  ]);
  assert.ok(drafts.every(order => order.limitPrice === ""));
  drafts[0].limitPrice = "300";
  drafts[1].limitPrice = "200";
  const orders = allocateOrders(drafts, 1100);
  assert.deepEqual(orders.map(o => o.quantity), [2.2, 1.65, 0]);
  assert.equal(orders[2].included, false);
  assert.equal(orders[2].limitPrice, '');
  assert.equal(orders.reduce((sum,o) => sum + o.quantity * Number(o.limitPrice), 0), 990);
});
test('sizing uses edited limits, handles small allocations, rejects missing prices', () => {
  const draft = draftOrders([{ticker:'AAA', country:'USA', price:'$1,000.00', portfolio_weight:100}]);
  assert.throws(() => allocateOrders(draft, 999), /limit price/);
  draft[0].limitPrice = "1000";
  assert.equal(allocateOrders(draft, 999)[0].quantity, 0.999);
  draft[0].limitPrice = '25';
  assert.equal(allocateOrders(draft, 1000)[0].quantity, 40);
  draft[0].limitPrice = '';
  assert.throws(() => allocateOrders(draft, 1000), /limit price/);
  assert.throws(() => allocateOrders(draft, NaN), /budget/);
});

test('small budget supports fractional allocations without exceeding target', () => {
  const draft = draftOrders([{ticker:'GOOG', country:'USA', price:'341.76', portfolio_weight:21.8753}]);
  draft[0].limitPrice = "341.76";
  const quantity = allocateOrders(draft, 100)[0].quantity;
  assert.equal(quantity, 0.064);
  assert.ok(quantity * 341.76 <= 21.8753);
});

test('Berkshire uses IBKR spaces while retaining source tickers', () => {
  const orders = draftOrders(['BRK-B', 'BRK.A', 'AAPL'].map(ticker => ({ticker, country:'USA', portfolio_weight:10})));
  assert.deepEqual(orders.map(order => order.symbol), ['BRK B', 'BRK A', 'AAPL']);
  assert.deepEqual(orders.map(order => order.sourceSymbol), ['BRK-B', 'BRK.A', 'AAPL']);
});

test('all hyphens become spaces without changing price-source tickers', () => {
  const tickers = ['BF-B', 'HEI-A', 'AAA-B-C', 'AAPL'];
  const orders = draftOrders(tickers.map(ticker => ({ticker, country:'USA', portfolio_weight:25})));
  assert.deepEqual(orders.map(order => order.symbol), ['BF B', 'HEI A', 'AAA B C', 'AAPL']);
  assert.deepEqual(orders.map(order => order.sourceSymbol), tickers);
});

test('buy-only balancing tops up deficits without selling or exceeding budget', async () => {
  const {allocateBuysOverPositions} = await import('./ibkr-export.mjs');
  const orders = ['AAA','BBB'].map(symbol => ({symbol, included:true, weight:50, limitPrice:'10'}));
  const positions = [{symbol:'AAA', type:'STK', currency:'USD', quantity:'10'}];
  const result = allocateBuysOverPositions(orders, 50, positions);
  assert.equal(result[0].quantity, 0);
  assert.ok(result[1].quantity > 4.999 && result[1].quantity <= 5);
  assert.ok(result.reduce((sum,o) => sum + o.quantity * 10, 0) <= 50);
  const balanced = allocateBuysOverPositions(orders, 100, []);
  assert.ok(balanced.every(o => Math.abs(o.quantity - 5) < 0.00011));
  assert.throws(() => allocateBuysOverPositions(orders, 50, [{...positions[0],quantity:'-1'}]), /short positions/);
  assert.throws(() => allocateBuysOverPositions(orders, 50, null), /Complete positions/);
});

test('buy-only balancing matches share classes and ignores non-target holdings', async () => {
  const {allocateBuysOverPositions} = await import('./ibkr-export.mjs');
  const orders = [{symbol:'BRK B', included:true, weight:25, limitPrice:'100'}, {symbol:'AAA', included:true, weight:25, limitPrice:'10'}];
  const positions = [{symbol:'BRK.B',type:'STK',currency:'USD',quantity:'1'}, {symbol:'OTHER',type:'STK',currency:'USD',quantity:'999'}];
  const result = allocateBuysOverPositions(orders, 50, positions);
  assert.equal(result[0].currentQuantity, 1);
  assert.equal(result[0].quantity, 0);
  assert.ok(result[1].quantity > 4.999);
});
