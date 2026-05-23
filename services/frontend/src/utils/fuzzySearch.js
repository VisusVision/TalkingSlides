export function normalizeSearchText(value) {
  return String(value || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9#\s-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function searchTokens(value) {
  return normalizeSearchText(value)
    .split(/\s+/)
    .map((token) => token.trim())
    .filter(Boolean);
}

function typoTolerance(token) {
  if (token.length < 4) return 0;
  if (token.length <= 7) return 1;
  return 2;
}

function levenshteinDistanceWithin(left, right, maxDistance) {
  if (Math.abs(left.length - right.length) > maxDistance) return maxDistance + 1;
  if (left === right) return 0;

  let previous = Array.from({ length: right.length + 1 }, (_, index) => index);
  for (let leftIndex = 1; leftIndex <= left.length; leftIndex += 1) {
    const current = [leftIndex];
    let rowMin = current[0];

    for (let rightIndex = 1; rightIndex <= right.length; rightIndex += 1) {
      const substitutionCost = left[leftIndex - 1] === right[rightIndex - 1] ? 0 : 1;
      const value = Math.min(
        previous[rightIndex] + 1,
        current[rightIndex - 1] + 1,
        previous[rightIndex - 1] + substitutionCost,
      );
      current[rightIndex] = value;
      rowMin = Math.min(rowMin, value);
    }

    if (rowMin > maxDistance) return maxDistance + 1;
    previous = current;
  }

  return previous[right.length];
}

function tokenScore(queryToken, textTokens) {
  if (textTokens.some((token) => token === queryToken)) {
    return { matched: true, score: 0, fuzzy: false };
  }

  if (textTokens.some((token) => token.includes(queryToken))) {
    return { matched: true, score: 10, fuzzy: false };
  }

  const tolerance = typoTolerance(queryToken);
  if (!tolerance) {
    return { matched: false, score: Number.POSITIVE_INFINITY, fuzzy: false };
  }

  let bestDistance = Number.POSITIVE_INFINITY;
  textTokens.forEach((token) => {
    if (token.length < 4) return;
    const distance = levenshteinDistanceWithin(queryToken, token, tolerance);
    bestDistance = Math.min(bestDistance, distance);
  });

  if (bestDistance <= tolerance) {
    return { matched: true, score: 40 + bestDistance, fuzzy: true };
  }

  return { matched: false, score: Number.POSITIVE_INFINITY, fuzzy: false };
}

export function scoreSearchText(text, query) {
  const normalizedQuery = normalizeSearchText(query);
  if (!normalizedQuery) {
    return { matched: true, score: 0, fuzzy: false, exact: true };
  }

  const normalizedText = normalizeSearchText(text);
  if (!normalizedText) {
    return { matched: false, score: Number.POSITIVE_INFINITY, fuzzy: false, exact: false };
  }

  if (normalizedText === normalizedQuery) {
    return { matched: true, score: 0, fuzzy: false, exact: true };
  }

  if (normalizedText.includes(normalizedQuery)) {
    return { matched: true, score: 5, fuzzy: false, exact: true };
  }

  const queryTokens = searchTokens(normalizedQuery);
  const textTokens = searchTokens(normalizedText);
  let totalScore = 0;
  let usedFuzzy = false;

  for (const queryToken of queryTokens) {
    const result = tokenScore(queryToken, textTokens);
    if (!result.matched) {
      return { matched: false, score: Number.POSITIVE_INFINITY, fuzzy: false, exact: false };
    }
    totalScore += result.score;
    usedFuzzy = usedFuzzy || result.fuzzy;
  }

  return {
    matched: true,
    score: totalScore + (usedFuzzy ? 100 : 20),
    fuzzy: usedFuzzy,
    exact: !usedFuzzy,
  };
}

export function fuzzySearch(items, query, getText) {
  const normalizedQuery = normalizeSearchText(query);
  const source = Array.isArray(items) ? items : [];
  if (!normalizedQuery) {
    return {
      items: source,
      matches: source.map((item, index) => ({
        item,
        index,
        score: 0,
        fuzzy: false,
        exact: true,
      })),
      query: '',
      exactCount: source.length,
      fuzzyCount: 0,
      isFuzzyOnly: false,
    };
  }

  const matches = source
    .map((item, index) => {
      const text = typeof getText === 'function' ? getText(item) : item;
      const result = scoreSearchText(text, normalizedQuery);
      return {
        item,
        index,
        ...result,
      };
    })
    .filter((match) => match.matched)
    .sort((left, right) => left.score - right.score || left.index - right.index);

  const exactCount = matches.filter((match) => !match.fuzzy).length;
  const fuzzyCount = matches.filter((match) => match.fuzzy).length;

  return {
    items: matches.map((match) => match.item),
    matches,
    query: normalizedQuery,
    exactCount,
    fuzzyCount,
    isFuzzyOnly: exactCount === 0 && fuzzyCount > 0,
  };
}
