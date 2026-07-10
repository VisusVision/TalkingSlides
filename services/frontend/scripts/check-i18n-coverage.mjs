import { APP_MESSAGES, STATIC_UI_MESSAGES } from '../src/i18n/messages.js';
import { SUPPORTED_APP_LOCALES } from '../src/i18n/locale.js';
import { fileURLToPath } from 'node:url';

const ALLOW_SAME_AS_ENGLISH = new Set([
  'Google',
  'VISUS',
  'VISUS VidLab',
  'Studio',
  'AI',
  'API',
  'DRM',
  'TTS',
  'MP4',
  'HLS',
  'CC',
  'Avatar',
  'Bio',
  'Details',
  'Engagement',
  'Message',
  'Mode',
  'Original',
  'Playlist',
  'Style',
  'Website',
  'Whiteboard',
  'example.com',
  'score',
]);

function flattenTree(value, prefix = '') {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return [[prefix, value]];
  }
  return Object.entries(value).flatMap(([key, child]) => (
    flattenTree(child, prefix ? `${prefix}.${key}` : key)
  ));
}

function valueAtPath(root, path) {
  return path.split('.').reduce((current, part) => current?.[part], root);
}

function interpolationTokens(value) {
  return Array.from(String(value).matchAll(/\{\{\s*([\w.]+)\s*\}\}|\{\s*([\w.]+)\s*\}/g))
    .map((match) => match[1] || match[2])
    .sort();
}

function assertCatalogCoverage(catalog, catalogName, errors) {
  const canonical = Object.fromEntries(flattenTree(catalog.en));
  const supportedCodes = SUPPORTED_APP_LOCALES.map((locale) => locale.code);

  for (const code of supportedCodes) {
    if (!catalog[code]) {
      errors.push(`${catalogName}: missing locale "${code}"`);
      continue;
    }

    const localeFlat = Object.fromEntries(flattenTree(catalog[code]));
    const localePaths = new Set(Object.keys(localeFlat));

    for (const [path, englishValue] of Object.entries(canonical)) {
      const localeValue = valueAtPath(catalog[code], path);
      localePaths.delete(path);

      if (localeValue === undefined) {
        errors.push(`${catalogName}.${code}.${path}: missing key`);
        continue;
      }
      if (localeValue === null || String(localeValue).trim() === '') {
        errors.push(`${catalogName}.${code}.${path}: empty value`);
        continue;
      }
      if (typeof localeValue === 'object' && typeof englishValue !== 'object') {
        errors.push(`${catalogName}.${code}.${path}: nested shape mismatch`);
        continue;
      }

      const englishTokens = interpolationTokens(englishValue);
      const localeTokens = interpolationTokens(localeValue);
      if (englishTokens.join('|') !== localeTokens.join('|')) {
        errors.push(`${catalogName}.${code}.${path}: interpolation mismatch (${englishTokens.join(',')} !== ${localeTokens.join(',')})`);
      }

      const sameAsEnglish = code !== 'en' && String(localeValue).trim() === String(englishValue).trim();
      if (sameAsEnglish && !ALLOW_SAME_AS_ENGLISH.has(String(englishValue).trim())) {
        errors.push(`${catalogName}.${code}.${path}: value still equals English`);
      }
    }

    for (const extraPath of localePaths) {
      errors.push(`${catalogName}.${code}.${extraPath}: extra key outside canonical English tree`);
    }
  }
}

function assertExactPhraseCoverage(catalog, catalogName, errors) {
  const canonical = catalog.en || {};
  const supportedCodes = SUPPORTED_APP_LOCALES.map((locale) => locale.code);

  for (const code of supportedCodes) {
    const localePhrases = catalog[code];
    if (!localePhrases) {
      errors.push(`${catalogName}: missing locale "${code}"`);
      continue;
    }

    const localeKeys = new Set(Object.keys(localePhrases));
    for (const [englishPhrase, englishValue] of Object.entries(canonical)) {
      localeKeys.delete(englishPhrase);
      const localeValue = localePhrases[englishPhrase];
      if (localeValue === undefined) {
        errors.push(`${catalogName}.${code}: missing phrase "${englishPhrase}"`);
        continue;
      }
      if (localeValue === null || String(localeValue).trim() === '') {
        errors.push(`${catalogName}.${code}: empty phrase "${englishPhrase}"`);
        continue;
      }

      const englishTokens = interpolationTokens(englishValue);
      const localeTokens = interpolationTokens(localeValue);
      if (englishTokens.join('|') !== localeTokens.join('|')) {
        errors.push(`${catalogName}.${code}: interpolation mismatch for "${englishPhrase}"`);
      }

      const sameAsEnglish = code !== 'en' && String(localeValue).trim() === String(englishValue).trim();
      if (sameAsEnglish && !ALLOW_SAME_AS_ENGLISH.has(String(englishValue).trim())) {
        errors.push(`${catalogName}.${code}: phrase "${englishPhrase}" still equals English`);
      }
    }

    for (const extraPhrase of localeKeys) {
      errors.push(`${catalogName}.${code}: extra phrase "${extraPhrase}" outside canonical English set`);
    }
  }
}

export function checkI18nCoverage() {
  const errors = [];
  assertCatalogCoverage(APP_MESSAGES, 'APP_MESSAGES', errors);
  assertExactPhraseCoverage(STATIC_UI_MESSAGES, 'STATIC_UI_MESSAGES', errors);
  return errors;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const errors = checkI18nCoverage();
  if (errors.length) {
    console.error(errors.join('\n'));
    process.exit(1);
  }
  console.log('i18n coverage OK');
}
