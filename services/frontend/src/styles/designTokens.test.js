import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const cssPath = resolve(dirname(fileURLToPath(import.meta.url)), 'globals.css');
const globalsCss = readFileSync(cssPath, 'utf8');

function expectTokenNames(source, tokenNames) {
  tokenNames.forEach((tokenName) => {
    expect(source, `${tokenName} should be defined`).toContain(`${tokenName}:`);
  });
}

describe('frontend design tokens', () => {
  it('keeps the existing semantic color token contract', () => {
    expectTokenNames(globalsCss, [
      '--bg',
      '--surface',
      '--surface-container',
      '--surface-container-low',
      '--surface-container-high',
      '--surface-container-highest',
      '--surface-container-lowest',
      '--surface-elevated',
      '--surface-muted',
      '--outline-variant',
      '--outline',
      '--border-subtle',
      '--text-primary',
      '--text-secondary',
      '--accent-primary',
      '--accent-secondary',
      '--accent-inverse',
      '--accent-gradient',
      '--hover-surface',
      '--hover-surface-strong',
      '--hover-accent-soft',
      '--glass-overlay',
      '--glass-stroke',
      '--feedback-danger-bg',
      '--feedback-danger-fg',
      '--status-success-bg',
      '--status-success-fg',
      '--status-danger-bg',
      '--status-danger-fg',
      '--status-info-bg',
      '--status-info-fg',
      '--status-warning-bg',
      '--status-warning-fg',
      '--modal-backdrop',
      '--video-stage-bg',
      '--bg-surface',
      '--bg-elevated',
    ]);
  });

  it('defines the Sprint 4 foundation token categories', () => {
    expectTokenNames(globalsCss, [
      '--space-0',
      '--space-0-5',
      '--space-1',
      '--space-1-5',
      '--space-2',
      '--space-3',
      '--space-4',
      '--space-5',
      '--space-6',
      '--space-8',
      '--space-10',
      '--space-12',
      '--space-16',
      '--radius-xs',
      '--radius-sm',
      '--radius-md',
      '--radius-lg',
      '--radius-xl',
      '--radius-2xl',
      '--radius-full',
      '--radius-control',
      '--radius-card',
      '--radius-dialog',
      '--radius-pill',
      '--shadow-xs',
      '--shadow-sm',
      '--shadow-md',
      '--shadow-lg',
      '--shadow-dialog',
      '--shadow-focus',
      '--duration-instant',
      '--duration-fast',
      '--duration-normal',
      '--duration-slow',
      '--ease-standard',
      '--ease-out',
      '--ease-in-out',
      '--ease-spring',
      '--control-height-sm',
      '--control-height-md',
      '--control-height-lg',
      '--icon-size-sm',
      '--icon-size-md',
      '--icon-size-lg',
      '--font-sans',
      '--font-display',
      '--text-xs',
      '--text-sm',
      '--text-base',
      '--text-lg',
      '--text-xl',
      '--text-2xl',
      '--leading-tight',
      '--leading-normal',
      '--leading-relaxed',
      '--tracking-tight',
    ]);
  });

  it('keeps dark mode and reduced-motion scopes active', () => {
    const darkBlock = globalsCss.match(/\.dark\s*{[\s\S]*?^}/m)?.[0] || '';

    expect(darkBlock).toContain('--bg:');
    expect(darkBlock).toContain('--text-primary:');
    expect(darkBlock).toContain('--shadow-dialog:');
    expect(globalsCss).toContain('@media (prefers-reduced-motion: reduce)');
    expect(globalsCss).toContain('.visus-page-progress');
    expect(globalsCss).toContain('.visus-loading-sheen::after');
  });

  it('keeps shared motion utilities token-backed and reduced-motion safe', () => {
    expect(globalsCss).toContain('.motion-interactive');
    expect(globalsCss).toContain('transition-duration: var(--duration-fast)');
    expect(globalsCss).toContain('animation: motion-fade var(--duration-fast) var(--ease-out) both');
    expect(globalsCss).toContain('animation: motion-scale-in var(--duration-fast) var(--ease-out) both');
    expect(globalsCss).toContain('animation: motion-slide-up var(--duration-normal) var(--ease-out) both');
    expect(globalsCss).toContain('animation: motion-page-enter var(--duration-normal) var(--ease-out) both');
    expect(globalsCss).toContain('animation: motion-popover-in var(--duration-fast) var(--ease-out) both');
    expect(globalsCss).toContain('.motion-nav-indicator');
    expect(globalsCss).toContain('.motion-disclosure');
    expect(globalsCss).toContain('.motion-exit');
    expect(globalsCss).toContain('.motion-interactive:hover');
    expect(globalsCss).toContain('.motion-slide-up');
    expect(globalsCss).toContain('.reduced-motion .motion-page-enter');
    expect(globalsCss).toContain('.reduced-motion .motion-popover-in');
    expect(globalsCss).toContain('transform: none !important');
  });
});
