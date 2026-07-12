import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import SurfaceCard from './SurfaceCard';

let host;
let root;

async function render(element) {
  await act(async () => {
    root.render(element);
  });
  return host;
}

describe('SurfaceCard', () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
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

  it('renders children with the default token surface and card padding', async () => {
    await render(
      <SurfaceCard data-testid="card">
        <span>Default content</span>
      </SurfaceCard>,
    );

    const card = host.querySelector('[data-testid="card"]');
    expect(card).toHaveTextContent('Default content');
    expect(card.className).toContain('rounded-card');
    expect(card.className).toContain('token-surface');
    expect(card.className).toContain('p-5');
    expect(card.className).toContain('duration-normal');
  });

  it('keeps elevated compatibility while supporting explicit variants', async () => {
    await render(
      <div>
        <SurfaceCard elevated data-testid="legacy-elevated">Legacy</SurfaceCard>
        <SurfaceCard variant="accent" padding="lg" data-testid="accent-card">Accent</SurfaceCard>
      </div>,
    );

    const legacy = host.querySelector('[data-testid="legacy-elevated"]');
    const accent = host.querySelector('[data-testid="accent-card"]');
    expect(legacy.className).toContain('token-surface-elevated');
    expect(legacy.className).toContain('shadow-token-sm');
    expect(accent.className).toContain('bg-[color:var(--hover-accent-soft)]');
    expect(accent.className).toContain('color-mix(in_srgb,var(--accent-primary),transparent_72%)');
    expect(accent.className).toContain('p-6');
  });

  it('merges root className and renders as the requested element', async () => {
    await render(
      <SurfaceCard as="article" className="custom-card min-h-24" data-testid="card">
        Article content
      </SurfaceCard>,
    );

    const card = host.querySelector('[data-testid="card"]');
    expect(card.tagName).toBe('ARTICLE');
    expect(card.className).toContain('custom-card');
    expect(card.className).toContain('min-h-24');
  });

  it('applies interactive and disabled card states', async () => {
    await render(
      <div>
        <SurfaceCard interactive data-testid="interactive">Interactive</SurfaceCard>
        <SurfaceCard disabled data-testid="disabled">Disabled</SurfaceCard>
      </div>,
    );

    const interactive = host.querySelector('[data-testid="interactive"]');
    const disabled = host.querySelector('[data-testid="disabled"]');
    expect(interactive.className).toContain('focus-ring');
    expect(interactive.className).toContain('hover:-translate-y-0.5');
    expect(disabled.className).toContain('pointer-events-none');
    expect(disabled).toHaveAttribute('aria-disabled', 'true');
  });

  it('renders Header, Body, Footer, Title, and Description with merged classes', async () => {
    await render(
      <SurfaceCard>
        <SurfaceCard.Header layout="stack" className="custom-header">
          <SurfaceCard.Title as="h3" size="md" className="custom-title">Card title</SurfaceCard.Title>
          <SurfaceCard.Description className="custom-description">Card description</SurfaceCard.Description>
        </SurfaceCard.Header>
        <SurfaceCard.Body className="custom-body">
          <p>Body copy</p>
        </SurfaceCard.Body>
        <SurfaceCard.Footer className="custom-footer">
          <button type="button">Action</button>
        </SurfaceCard.Footer>
      </SurfaceCard>,
    );

    const title = host.querySelector('h3');
    const description = host.querySelector('.custom-description');
    const body = host.querySelector('.custom-body');
    const footer = host.querySelector('.custom-footer');
    expect(host).toHaveTextContent('Body copy');
    expect(title).toHaveTextContent('Card title');
    expect(title.className).toContain('text-base');
    expect(title.className).toContain('custom-title');
    expect(description).toHaveTextContent('Card description');
    expect(description.className).toContain('text-[var(--text-secondary)]');
    expect(body.className).toContain('space-y-4');
    expect(footer.className).toContain('border-t');
    expect(footer).toHaveTextContent('Action');
  });
});
