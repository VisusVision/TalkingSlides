import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import Skeleton from './Skeleton';

let host;
let root;

async function render(element) {
  await act(async () => {
    root.render(element);
  });
  return host;
}

describe('Skeleton', () => {
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

  it('renders a non-interactive, aria-hidden skeleton shape with className merging', async () => {
    await render(<Skeleton data-testid="shape" className="h-10 custom-shape" rounded="full" />);

    const shape = host.querySelector('[data-testid="shape"]');
    expect(shape).toHaveAttribute('aria-hidden', 'true');
    expect(shape.className).toContain('visus-loading-sheen');
    expect(shape.className).toContain('pointer-events-none');
    expect(shape.className).toContain('rounded-full');
    expect(shape.className).toContain('custom-shape');
  });

  it('renders text, avatar, card, list, and table-row variants', async () => {
    await render(
      <div>
        <Skeleton.Text lines={3} className="copy-lines" />
        <Skeleton.Avatar size="lg" className="avatar-slot" />
        <Skeleton.Card className="card-slot" />
        <Skeleton.List count={2} itemClassName="list-card" />
        <table>
          <tbody>
            <Skeleton.TableRow columns={3} className="table-row" />
          </tbody>
        </table>
      </div>,
    );

    expect(host.querySelectorAll('.copy-lines .visus-loading-sheen')).toHaveLength(3);
    expect(host.querySelector('.avatar-slot').className).toContain('h-14');
    expect(host.querySelector('.card-slot').className).toContain('rounded-card');
    expect(host.querySelectorAll('.list-card')).toHaveLength(2);
    expect(host.querySelectorAll('.table-row td')).toHaveLength(3);
  });

  it('keeps animation compatibility on the token-backed reduced-motion hook', async () => {
    await render(<Skeleton className="h-4 w-20" />);

    expect(host.querySelector('.visus-loading-sheen')).toBeTruthy();
  });
});
