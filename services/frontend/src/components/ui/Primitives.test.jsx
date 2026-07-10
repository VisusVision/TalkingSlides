import { act, createRef } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Badge from './Badge';
import Input from './Input';
import Select from './Select';
import SurfaceCard from './SurfaceCard';
import Textarea from './Textarea';

let host;
let root;

function setNativeValue(element, value) {
  const valueSetter = Object.getOwnPropertyDescriptor(element, 'value')?.set;
  const prototypeSetter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), 'value')?.set;

  if (prototypeSetter && valueSetter !== prototypeSetter) {
    prototypeSetter.call(element, value);
    return;
  }

  element.value = value;
}

async function render(element) {
  await act(async () => {
    root.render(element);
  });
  return host;
}

describe('shared UI primitives', () => {
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

  it('renders token-backed inputs with native attributes, refs, classes, and handlers', async () => {
    const inputRef = createRef();
    const handleChange = vi.fn();

    await render(
      <div>
        <Input
          ref={inputRef}
          aria-label="Lesson title"
          className="custom-input"
          invalid
          name="title"
          onChange={handleChange}
          placeholder="Title"
          size="lg"
          type="text"
        />
        <Input aria-label="Disabled title" disabled />
      </div>,
    );

    const input = host.querySelector('input[aria-label="Lesson title"]');
    const disabledInput = host.querySelector('input[aria-label="Disabled title"]');
    expect(inputRef.current).toBe(input);
    expect(disabledInput).toBeDisabled();
    expect(input).toHaveAttribute('name', 'title');
    expect(input).toHaveAttribute('placeholder', 'Title');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input.className).toContain('rounded-control');
    expect(input.className).toContain('h-control-lg');
    expect(input.className).toContain('duration-normal');
    expect(input.className).toContain('placeholder:text-[var(--outline)]');
    expect(input.className).toContain('custom-input');

    await act(async () => {
      setNativeValue(input, 'Draft title');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });

    expect(handleChange).toHaveBeenCalled();
  });

  it('renders textarea with native rows, invalid state, and class passthrough', async () => {
    await render(
      <Textarea
        aria-label="Notes"
        className="min-h-32"
        invalid
        rows={5}
        defaultValue="Existing note"
      />,
    );

    const textarea = host.querySelector('textarea');
    expect(textarea).toHaveAttribute('rows', '5');
    expect(textarea).toHaveAttribute('aria-invalid', 'true');
    expect(textarea.value).toBe('Existing note');
    expect(textarea.className).toContain('rounded-control');
    expect(textarea.className).toContain('min-h-32');
  });

  it('renders native select controls with size variants and change handlers', async () => {
    const handleChange = vi.fn();

    await render(
      <Select aria-label="Category" defaultValue="design" onChange={handleChange} size="sm">
        <option value="ai">AI</option>
        <option value="design">Design</option>
      </Select>,
    );

    const select = host.querySelector('select');
    expect(select.value).toBe('design');
    expect(select.className).toContain('rounded-control');
    expect(select.className).toContain('h-control-sm');

    await act(async () => {
      select.value = 'ai';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });

    expect(handleChange).toHaveBeenCalled();
  });

  it('keeps SurfaceCard element, elevation, props, and className contracts', async () => {
    await render(
      <SurfaceCard as="article" elevated className="space-y-4" data-testid="card">
        Card body
      </SurfaceCard>,
    );

    const card = host.querySelector('[data-testid="card"]');
    expect(card.tagName).toBe('ARTICLE');
    expect(card).toHaveTextContent('Card body');
    expect(card.className).toContain('rounded-card');
    expect(card.className).toContain('token-surface-elevated');
    expect(card.className).toContain('shadow-token-sm');
    expect(card.className).toContain('space-y-4');
  });

  it('renders badges with semantic variants, element override, and fallback tone', async () => {
    await render(
      <div>
        <Badge variant="success" className="custom-badge">Ready</Badge>
        <Badge as="strong" variant="missing">Fallback</Badge>
      </div>,
    );

    const [success, fallback] = host.querySelectorAll('span, strong');
    expect(success).toHaveTextContent('Ready');
    expect(success.className).toContain('rounded-pill');
    expect(success.className).toContain('bg-[color:var(--status-success-bg)]');
    expect(success.className).toContain('custom-badge');
    expect(fallback.tagName).toBe('STRONG');
    expect(fallback.className).toContain('bg-[var(--surface-container-high)]');
  });
});
