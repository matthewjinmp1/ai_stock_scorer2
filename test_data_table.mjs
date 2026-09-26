import test from 'node:test';
import assert from 'node:assert/strict';
import { reorderedColumns, makeColumnsDraggable } from './data-table.js';

test('column moves preserve hidden columns and support both drop edges', () => {
  const order = ['a', 'hidden', 'b', 'c'];
  assert.deepEqual(reorderedColumns(order, 'a', 'c', true), ['hidden', 'b', 'c', 'a']);
  assert.deepEqual(reorderedColumns(order, 'c', 'a'), ['c', 'a', 'hidden', 'b']);
  assert.deepEqual(reorderedColumns(order, 'a', 'a'), order);
  assert.deepEqual(reorderedColumns(order, 'unknown', 'a'), order);
});

test('standalone tables keep headers and refreshed rows aligned and persist drops', () => {
  const saved = new Map([['test-order', '["Price","Company"]']]);
  globalThis.localStorage = {getItem: key => saved.get(key), setItem: (key, value) => saved.set(key, value)};
  const cell = textContent => ({textContent, dataset: {}, colSpan: 1,
    classList: {add() {}, remove() {}}, getBoundingClientRect: () => ({left: 0, width: 100}),
    closest() {return this;}});
  const row = values => ({cells: values.map(cell), appendChild(item) {
    this.cells.splice(this.cells.indexOf(item), 1); this.cells.push(item);
  }});
  const head = row(['Company', 'Price']);
  const listeners = {};
  head.addEventListener = (name, callback) => {listeners[name] = callback;};
  head.querySelectorAll = () => [];
  let rows = [row(['AAA', '$10'])];
  const table = {querySelector: () => head, querySelectorAll: () => rows};
  const apply = makeColumnsDraggable(table, 'test-order');
  assert.deepEqual(head.cells.map(c => c.textContent), ['Price', 'Company']);
  assert.deepEqual(rows[0].cells.map(c => c.textContent), ['$10', 'AAA']);
  rows = [row(['BBB', '$20'])]; apply();
  assert.deepEqual(rows[0].cells.map(c => c.textContent), ['$20', 'BBB']);
  const event = target => ({target, clientX: 0, preventDefault() {}, dataTransfer: {setData() {}}});
  listeners.dragstart(event(head.cells[1])); listeners.drop(event(head.cells[0]));
  assert.equal(saved.get('test-order'), '["Company","Price"]');
  assert.deepEqual(rows[0].cells.map(c => c.textContent), ['BBB', '$20']);
  const empty = row(['No rows']); empty.cells[0].colSpan = 2; rows = [empty]; apply();
  assert.equal(empty.cells[0].textContent, 'No rows');
});
