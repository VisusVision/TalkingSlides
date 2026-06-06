export const SOCIAL_LINK_FIELDS = [
  {
    key: 'x',
    label: 'X',
    placeholder: '@janedoe',
    helper: '@janedoe or x.com/janedoe',
  },
  {
    key: 'instagram',
    label: 'Instagram',
    placeholder: '@janedoe',
    helper: '@janedoe or instagram.com/janedoe',
  },
  {
    key: 'youtube',
    label: 'YouTube',
    placeholder: 'youtube.com/@channel',
    helper: '@channel, youtube.com/@channel, or youtube.com/channel/...',
  },
  {
    key: 'linkedin',
    label: 'LinkedIn',
    placeholder: 'janedoe',
    helper: 'janedoe, linkedin.com/in/janedoe, or linkedin.com/company/example',
  },
  {
    key: 'github',
    label: 'GitHub',
    placeholder: 'githubuser',
    helper: 'githubuser or github.com/githubuser',
  },
  {
    key: 'facebook',
    label: 'Facebook',
    placeholder: '@janedoe',
    helper: '@janedoe or facebook.com/janedoe',
  },
];

const VALUE_MAX_LENGTH = 300;
const HANDLE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
const PRIVATE_HOST_PATTERNS = [
  /^localhost$/i,
  /\.localhost$/i,
  /^127\./,
  /^10\./,
  /^192\.168\./,
  /^172\.(1[6-9]|2\d|3[0-1])\./,
  /^169\.254\./,
  /^0\.0\.0\.0$/,
  /^::1$/,
  /^fc00:/i,
  /^fd[0-9a-f]{2}:/i,
  /^fe80:/i,
];

function ensureHttps(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  if (/^[a-z][a-z0-9+.-]*:/i.test(raw)) return raw;
  return `https://${raw.replace(/^\/+/, '')}`;
}

function hostWithoutWww(url) {
  const parsed = new URL(url);
  const hostname = String(parsed.hostname || '').trim().toLowerCase();
  return hostname.startsWith('www.') ? hostname.slice(4) : hostname;
}

function rejectUnsafeUrl(url, label) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    throw new Error(`${label} must be a valid URL.`);
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error(`${label} must use http or https.`);
  }
  const hostname = String(parsed.hostname || '').trim().toLowerCase();
  if (!hostname) {
    throw new Error(`${label} must include a valid host.`);
  }
  if (PRIVATE_HOST_PATTERNS.some((pattern) => pattern.test(hostname))) {
    throw new Error(`${label} cannot point to localhost or a private network.`);
  }
}

function stripHandle(value) {
  const raw = String(value || '').trim().replace(/^@/, '').replace(/^\/+|\/+$/g, '');
  return raw.trim();
}

export function socialLinkValue(links, key) {
  const source = links && typeof links === 'object' ? links : {};
  if (key === 'x') {
    return String(source.x || source.twitter || '').trim();
  }
  return String(source[key] || '').trim();
}

export function normalizeWebsiteInput(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  if (raw.length > VALUE_MAX_LENGTH) {
    throw new Error('Website is too long.');
  }
  const url = ensureHttps(raw);
  rejectUnsafeUrl(url, 'Website');
  return url;
}

export function normalizeSocialInput(key, value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  if (raw.length > VALUE_MAX_LENGTH) {
    throw new Error(`${key} is too long.`);
  }
  if (/^(javascript|data|file):/i.test(raw)) {
    throw new Error('Use an http(s) URL or a handle.');
  }

  const canonicalKey = key === 'twitter' ? 'x' : key;
  if (canonicalKey === 'website') return normalizeWebsiteInput(raw);

  const hostByKey = {
    instagram: 'instagram.com',
    x: 'x.com',
    youtube: 'youtube.com',
    linkedin: 'linkedin.com',
    github: 'github.com',
    facebook: 'facebook.com',
  };
  const canonicalHost = hostByKey[canonicalKey];
  if (!canonicalHost) return '';

  const candidateUrl = ensureHttps(raw);
  let hostname = '';
  let path = '';
  try {
    const parsed = new URL(candidateUrl);
    hostname = hostWithoutWww(candidateUrl);
    path = String(parsed.pathname || '').replace(/^\/+|\/+$/g, '');
  } catch {
    hostname = '';
  }
  if (canonicalKey === 'x' && hostname === 'twitter.com') {
    hostname = 'x.com';
  }

  if (hostname === canonicalHost) {
    rejectUnsafeUrl(candidateUrl, canonicalKey);
    if (canonicalKey === 'linkedin') {
      if (!path || path.startsWith('in/') || path.startsWith('company/')) {
        return path ? `https://linkedin.com/${path}` : 'https://linkedin.com';
      }
      throw new Error('Use linkedin.com/in/name or linkedin.com/company/name.');
    }
    if (canonicalKey === 'youtube') {
      return path ? `https://youtube.com/${path}` : 'https://youtube.com';
    }
    if (canonicalKey === 'x') {
      return path ? `https://x.com/${path}` : 'https://x.com';
    }
    return path ? `https://${canonicalHost}/${path}` : `https://${canonicalHost}`;
  }

  const handle = stripHandle(raw);
  if (handle.includes('/') || !HANDLE_PATTERN.test(handle)) {
    throw new Error(`Enter a handle or ${canonicalHost} URL.`);
  }
  if (canonicalKey === 'youtube') return `https://youtube.com/@${handle}`;
  if (canonicalKey === 'linkedin') return `https://linkedin.com/in/${handle}`;
  return `https://${canonicalHost}/${handle}`;
}

export function validatePublicProfileDraft(draft) {
  const errors = {};
  try {
    normalizeWebsiteInput(draft?.website_url);
  } catch (error) {
    errors.website_url = error.message || 'Enter a valid website.';
  }

  const email = String(draft?.contact_email || '').trim();
  if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    errors.contact_email = 'Enter a valid email address.';
  }

  for (const field of SOCIAL_LINK_FIELDS) {
    try {
      normalizeSocialInput(field.key, draft?.social_links?.[field.key]);
    } catch (error) {
      errors[`social_links.${field.key}`] = error.message || `Enter a valid ${field.label} link.`;
    }
  }
  return errors;
}

export function normalizedPublicProfilePayload(draft) {
  const socialLinks = {};
  for (const field of SOCIAL_LINK_FIELDS) {
    const normalized = normalizeSocialInput(field.key, draft?.social_links?.[field.key]);
    if (normalized) socialLinks[field.key] = normalized;
  }
  return {
    first_name: String(draft?.first_name || '').trim(),
    last_name: String(draft?.last_name || '').trim(),
    display_name: String(draft?.display_name || '').trim(),
    bio: String(draft?.bio || '').trim(),
    website_url: normalizeWebsiteInput(draft?.website_url),
    contact_email: String(draft?.contact_email || '').trim(),
    social_links: socialLinks,
    is_public_profile: Boolean(draft?.is_public_profile),
  };
}

function firstError(value) {
  if (Array.isArray(value)) return String(value[0] || '').trim();
  if (typeof value === 'string') return value.trim();
  return '';
}

export function profileFieldErrorsFromApi(details) {
  const errors = {};
  const source = details && typeof details === 'object' ? details : {};
  for (const key of ['website_url', 'contact_email', 'display_name', 'bio', 'first_name', 'last_name']) {
    const message = firstError(source[key]);
    if (message) errors[key] = message;
  }
  const socialErrors = source.social_links;
  if (socialErrors && typeof socialErrors === 'object' && !Array.isArray(socialErrors)) {
    for (const [key, value] of Object.entries(socialErrors)) {
      const canonicalKey = key === 'twitter' ? 'x' : key;
      const message = firstError(value);
      if (message) errors[`social_links.${canonicalKey}`] = message;
    }
  } else {
    const message = firstError(socialErrors);
    if (message) errors.social_links = message;
  }
  return errors;
}
