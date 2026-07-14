import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ProductGuidance from './ProductGuidance';
import { productGuidanceCopy, productGuidanceLocale } from './productGuidanceCopy';

let host;
let root;

async function render(element) {
  await act(async () => {
    root.render(element);
  });
  return host;
}

describe('ProductGuidance', () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    document.documentElement.lang = 'en';
    document.documentElement.dir = '';
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    host.remove();
  });

  it('renders status, title, description, actions, items, and class passthrough accessibly', async () => {
    const onPrimary = vi.fn();
    const onSecondary = vi.fn();
    const onItem = vi.fn();

    await render(
      <ProductGuidance
        status="needs-attention"
        eyebrow="Learning guidance"
        title="Continue editing your draft."
        description="A real draft exists in your library."
        className="custom-guidance"
        primaryAction={{ label: 'Open draft', onClick: onPrimary }}
        secondaryActions={[{ label: 'Review later', onClick: onSecondary }]}
        items={[{
          key: 'draft',
          status: 'processing',
          title: 'Draft render',
          description: 'Render is processing.',
          metadata: 'Real status',
          action: { label: 'Open item', onClick: onItem },
        }]}
      />,
    );

    const guidance = host.querySelector('[data-testid="product-guidance"]');
    expect(guidance).toHaveAttribute('data-status', 'needs-attention');
    expect(guidance).toHaveAttribute('aria-labelledby');
    expect(guidance.className).toContain('custom-guidance');
    expect(host.querySelector('h2')).toHaveTextContent('Continue editing your draft.');
    expect(host).toHaveTextContent('Needs attention');
    expect(host.querySelector('ul[aria-label="Guidance items"]')).toBeTruthy();

    const buttons = host.querySelectorAll('button');
    expect(buttons).toHaveLength(3);
    await act(async () => buttons[0].click());
    await act(async () => buttons[1].click());
    await act(async () => buttons[2].click());
    expect(onPrimary).toHaveBeenCalledTimes(1);
    expect(onSecondary).toHaveBeenCalledTimes(1);
    expect(onItem).toHaveBeenCalledTimes(1);
  });

  it('does not render actions without a real callback or href', async () => {
    await render(
      <ProductGuidance
        status="ready"
        title="Ready"
        description="No callback is available."
        primaryAction={{ label: 'Missing callback' }}
        secondaryActions={[{ label: 'Also missing' }]}
        items={[{ key: 'item', title: 'Item', action: { label: 'No callback' } }]}
      />,
    );

    expect(host.querySelectorAll('button')).toHaveLength(0);
    expect(host).not.toHaveTextContent('Missing callback');
  });

  it('localizes Turkish and Arabic status copy without normal-path English leakage', async () => {
    document.documentElement.lang = 'tr-TR';
    let copy = productGuidanceCopy('tr-TR');

    await render(
      <ProductGuidance
        copy={copy}
        status="completed"
        title={copy.notificationsCaughtUpTitle}
        description={copy.notificationsCaughtUpDescription}
      />,
    );

    expect(host.textContent).toContain(copy.statusCompleted);
    expect(host.textContent).toContain(copy.notificationsCaughtUpTitle);
    expect(host.textContent).not.toContain('You are all caught up');

    document.documentElement.lang = 'ar';
    document.documentElement.dir = 'rtl';
    copy = productGuidanceCopy('ar');
    await render(
      <ProductGuidance
        copy={copy}
        status="failed"
        title={copy.notificationsFailedTitle}
        description={copy.notificationsFailedDescription}
      />,
    );

    expect(host.textContent).toContain(copy.statusFailed);
    expect(host.textContent).toContain(copy.notificationsFailedTitle);
    expect(host.textContent).not.toContain('A render update needs attention');
  });

  it('declares all supported locales', () => {
    ['en', 'tr', 'es', 'fr', 'de', 'it', 'pt', 'ru', 'ja', 'ko', 'zh', 'ar'].forEach((locale) => {
      expect(productGuidanceLocale(locale)).toBe(locale);
      expect(productGuidanceCopy(locale).eyebrow).toBeTruthy();
    });
  });
});
