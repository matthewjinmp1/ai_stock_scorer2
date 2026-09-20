import test from 'node:test';
import assert from 'node:assert/strict';
import { draftOrders, allocateOrders } from './ibkr-export.mjs';

test('budget sizing rounds down and leaves excluded weights unallocated', () => {
  const drafts = draftOrders([
    {ticker:'AAA', country:'USA', price:'$300', portfolio_weight:60},
    {ticker:'BBB', country:'USA', price:'$200', portfolio_weight:30},
    {ticker:'CCC', country:'UK', price:'$100', portfolio_weight:10},
  ]);
  const orders = allocateOrders(drafts, 1100);
  assert.deepEqual(orders.map(o => o.quantity), [2, 1, 0]);
  assert.equal(orders[2].included, false);
  assert.equal(orders[2].limitPrice, '');
  assert.equal(orders.reduce((sum,o) => sum + o.quantity * Number(o.limitPrice), 0), 800);
});
test('sizing uses edited limits, handles small allocations, rejects missing prices', () => {
  const draft = draftOrders([{ticker:'AAA', country:'USA', price:'$1,000.00', portfolio_weight:100}]);
  assert.equal(allocateOrders(draft, 999)[0].quantity, 0);
  draft[0].limitPrice = '25';
  assert.equal(allocateOrders(draft, 1000)[0].quantity, 40);
  draft[0].limitPrice = '';
  assert.throws(() => allocateOrders(draft, 1000), /limit price/);
  assert.throws(() => allocateOrders(draft, NaN), /budget/);
});
