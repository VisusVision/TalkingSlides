import test from 'node:test';
import assert from 'node:assert/strict';
import { fuzzySearch } from './fuzzySearch.js';

const lessons = [
  { title: 'Biology Foundations' },
  { title: 'Advanced Biotechnology' },
  { title: 'Modern Literature' },
];

test('fuzzy helper finds a one-letter typo', () => {
  const result = fuzzySearch(lessons, 'biolgy', (lesson) => lesson.title);
  assert.equal(result.items[0].title, 'Biology Foundations');
  assert.equal(result.isFuzzyOnly, true);
});

test('exact matches rank before fuzzy matches', () => {
  const result = fuzzySearch(lessons, 'biology', (lesson) => lesson.title);
  assert.equal(result.items[0].title, 'Biology Foundations');
  assert.equal(result.matches[0].fuzzy, false);
});

test('unrelated query does not overmatch', () => {
  const result = fuzzySearch(lessons, 'zzzzzz', (lesson) => lesson.title);
  assert.equal(result.items.length, 0);
});
