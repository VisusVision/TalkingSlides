import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { PageContainer, PageHeader, PageToolbar } from './PageLayout';

let host;
let root;

async function render(element) {
  await act(async () => {
    root.render(element);
  });
  return host;
}

describe('page layout primitives', () => {
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

  it('renders page containers with width variants, rhythm, props, and class merging', async () => {
    await render(
      <PageContainer as="main" width="standard" gap="lg" className="custom-page" data-testid="page">
        Content
      </PageContainer>,
    );

    const page = host.querySelector('[data-testid="page"]');
    expect(page.tagName).toBe('MAIN');
    expect(page).toHaveTextContent('Content');
    expect(page.className).toContain('max-w-6xl');
    expect(page.className).toContain('gap-7');
    expect(page.className).toContain('py-6');
    expect(page.className).toContain('custom-page');
    expect(page.className).not.toContain('motion-page-enter');
  });

  it('adds opt-in page entrance motion without changing the default container contract', async () => {
    await render(
      <PageContainer as="main" motion="enter" data-testid="page">
        Animated content
      </PageContainer>,
    );

    const page = host.querySelector('[data-testid="page"]');
    expect(page.className).toContain('motion-page-enter');
    expect(page.className).toContain('max-w-[1500px]');
  });

  it('renders semantic page headers with title, description, actions, and custom heading element', async () => {
    await render(
      <PageHeader
        eyebrow="Library"
        title="Your Learning Hub"
        titleAs="h2"
        titleSize="headline"
        description="Continue watched lessons."
        motion="fade"
        actions={<button type="button">New lesson</button>}
        data-testid="header"
      />,
    );

    const header = host.querySelector('[data-testid="header"]');
    expect(header.tagName).toBe('HEADER');
    expect(host.querySelector('h2')).toHaveTextContent('Your Learning Hub');
    expect(host.querySelector('h2').className).toContain('headline-md');
    expect(header.className).toContain('motion-fade');
    expect(header).toHaveTextContent('Library');
    expect(header).toHaveTextContent('Continue watched lessons.');
    expect(header.querySelector('button')).toHaveTextContent('New lesson');
  });

  it('renders toolbars with surface, sticky, children, props, and class merging', async () => {
    await render(
      <PageToolbar sticky motion="fade" className="custom-toolbar" aria-label="Filters">
        <div>Tabs</div>
        <p>12 results</p>
      </PageToolbar>,
    );

    const toolbar = host.querySelector('section[aria-label="Filters"]');
    expect(toolbar.className).toContain('rounded-card');
    expect(toolbar.className).toContain('sticky');
    expect(toolbar.className).toContain('top-20');
    expect(toolbar.className).toContain('motion-fade');
    expect(toolbar.className).toContain('custom-toolbar');
    expect(toolbar).toHaveTextContent('Tabs');
    expect(toolbar).toHaveTextContent('12 results');
  });
});

