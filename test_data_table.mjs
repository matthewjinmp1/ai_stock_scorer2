import test from 'node:test';
import assert from 'node:assert/strict';
import { reorderedColumns } from './data-table.js';

test('column moves preserve hidden columns and support both drop edges', () => {
  const order = ['a', 'hidden', 'b', 'c'];
  assert.deepEqual(reorderedColumns(order, 'a', 'c', true), ['hidden', 'b', 'c', 'a']);
  assert.deepEqual(reorderedColumns(order, 'c', 'a'), ['c', 'a', 'hidden', 'b']);
  assert.deepEqual(reorderedColumns(order, 'a', 'a'), order);
  assert.deepEqual(reorderedColumns(order, 'unknown', 'a'), order);
});
