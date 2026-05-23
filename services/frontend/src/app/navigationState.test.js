import test from 'node:test';
import assert from 'node:assert/strict';
import {
  mergeSectionStateInStore,
  resetSectionInStore,
} from './navigationState.js';

test('state keys are independent', () => {
  let store = {};
  store = mergeSectionStateInStore(store, 'dashboard', { search: 'biology' });
  store = mergeSectionStateInStore(store, 'library', { search: 'abstract' });

  assert.equal(store.dashboard.state.search, 'biology');
  assert.equal(store.library.state.search, 'abstract');
});

test('reset action clears only selected section', () => {
  let store = {};
  store = mergeSectionStateInStore(store, 'dashboard', { search: 'biology' });
  store = mergeSectionStateInStore(store, 'library', { search: 'abstract' });
  store = resetSectionInStore(store, 'dashboard');

  assert.equal(store.dashboard, undefined);
  assert.equal(store.library.state.search, 'abstract');
});
