export async function copyTextToClipboard(text) {
  const value = String(text || '');
  if (!value.trim()) {
    throw new Error('Nothing to copy');
  }

  if (navigator?.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      // Fall through to the textarea fallback for restricted browser surfaces.
    }
  }

  if (typeof document === 'undefined' || typeof document.execCommand !== 'function') {
    throw new Error('Clipboard copy is unavailable');
  }

  const textarea = document.createElement('textarea');
  textarea.value = value;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  textarea.style.top = '0';
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();

  try {
    const copied = document.execCommand('copy');
    if (!copied) throw new Error('Clipboard copy was rejected');
    return true;
  } finally {
    document.body.removeChild(textarea);
  }
}
