import { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Info,
  Loader2,
  X,
  XCircle,
} from 'lucide-react';

const TOAST_LIMIT = 4;
const TOAST_REMOVE_DELAY = 260;

const VARIANT_CONFIG = {
  success: {
    icon: CheckCircle2,
    role: 'status',
    live: 'polite',
    label: 'Success',
    iconClass: 'bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]',
    borderClass: 'border-[color:color-mix(in_srgb,var(--status-success-fg),transparent_68%)]',
  },
  error: {
    icon: XCircle,
    role: 'alert',
    live: 'assertive',
    label: 'Error',
    iconClass: 'bg-[color:var(--status-danger-bg)] text-[color:var(--status-danger-fg)]',
    borderClass: 'border-[color:color-mix(in_srgb,var(--status-danger-fg),transparent_62%)]',
  },
  warning: {
    icon: AlertTriangle,
    role: 'status',
    live: 'polite',
    label: 'Warning',
    iconClass: 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]',
    borderClass: 'border-[color:color-mix(in_srgb,var(--status-warning-fg),transparent_62%)]',
  },
  info: {
    icon: Info,
    role: 'status',
    live: 'polite',
    label: 'Info',
    iconClass: 'bg-[color:var(--status-info-bg)] text-[color:var(--status-info-fg)]',
    borderClass: 'border-[color:color-mix(in_srgb,var(--status-info-fg),transparent_66%)]',
  },
  loading: {
    icon: Loader2,
    role: 'status',
    live: 'polite',
    label: 'Loading',
    iconClass: 'bg-[color:var(--surface-container-highest)] text-[color:var(--accent-primary)]',
    borderClass: 'border-[color:var(--border-subtle)]',
  },
};

const DEFAULT_DURATIONS = {
  success: 4200,
  error: 6200,
  warning: 5200,
  info: 4200,
  loading: Infinity,
};

let toastCounter = 0;
let state = [];
const listeners = new Set();

function joinClasses(...parts) {
  return parts.filter(Boolean).join(' ');
}

function notify() {
  listeners.forEach((listener) => listener(state));
}

function normalizeVariant(variant) {
  return VARIANT_CONFIG[variant] ? variant : 'info';
}

function dismissToast(id) {
  state = state.map((item) => (
    item.id === id ? { ...item, dismissed: true } : item
  ));
  notify();

  window.setTimeout(() => {
    state = state.filter((item) => item.id !== id);
    notify();
  }, TOAST_REMOVE_DELAY);
}

function upsertToast(variant, message, options = {}) {
  const resolvedVariant = normalizeVariant(variant);
  const id = options.id || `toast-${Date.now()}-${toastCounter += 1}`;
  const duration = options.duration ?? DEFAULT_DURATIONS[resolvedVariant];
  const nextToast = {
    id,
    variant: resolvedVariant,
    message: String(message || ''),
    description: options.description ? String(options.description) : '',
    duration,
    progress: Number.isFinite(options.progress) ? Math.max(0, Math.min(100, Number(options.progress))) : null,
    className: options.className || '',
    dismissed: false,
  };

  state = [
    nextToast,
    ...state.filter((item) => item.id !== id),
  ];
  notify();
  return id;
}

export const toast = {
  success: (message, options) => upsertToast('success', message, options),
  error: (message, options) => upsertToast('error', message, options),
  warning: (message, options) => upsertToast('warning', message, options),
  info: (message, options) => upsertToast('info', message, options),
  loading: (message, options) => upsertToast('loading', message, options),
  dismiss: dismissToast,
  clear: () => {
    state = [];
    notify();
  },
  subscribe: (listener) => {
    listeners.add(listener);
    listener(state);
    return () => listeners.delete(listener);
  },
  getSnapshot: () => state,
};

function ToastItem({ item, onDismiss }) {
  const config = VARIANT_CONFIG[item.variant] || VARIANT_CONFIG.info;
  const Icon = config.icon;
  const progressStyle = item.progress === null ? null : { width: `${item.progress}%` };

  useEffect(() => {
    if (item.dismissed || !Number.isFinite(item.duration) || item.duration <= 0) {
      return undefined;
    }
    const timer = window.setTimeout(() => onDismiss(item.id), item.duration);
    return () => window.clearTimeout(timer);
  }, [item.dismissed, item.duration, item.id, onDismiss]);

  const handleKeyDown = (event) => {
    if (event.key === 'Escape' || event.key === 'Delete' || event.key === 'Backspace') {
      event.stopPropagation();
      onDismiss(item.id);
    }
  };

  return (
    <article
      role={config.role}
      aria-live={config.live}
      aria-atomic="true"
      tabIndex={0}
      data-toast-variant={item.variant}
      data-toast-dismissed={item.dismissed ? 'true' : 'false'}
      dir="auto"
      onKeyDown={handleKeyDown}
      className={joinClasses(
        'visus-toast motion-slide-up group relative grid grid-cols-[auto_1fr_auto] gap-3 overflow-hidden rounded-token-xl border bg-[color:var(--surface-container-lowest)] p-3 text-start text-[var(--text-primary)] shadow-token-md outline-none transition-[opacity,transform,box-shadow] duration-normal ease-standard focus-ring',
        config.borderClass,
        item.dismissed && 'motion-exit',
        item.className,
      )}
    >
      <span className={joinClasses(
        'mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full',
        config.iconClass,
      )}
      >
        <Icon
          size={16}
          aria-hidden="true"
          className={item.variant === 'loading' ? 'visus-toast-spin' : ''}
        />
      </span>
      <span className="min-w-0">
        <span className="sr-only">{config.label}: </span>
        <span className="block text-sm font-semibold leading-5">{item.message}</span>
        {item.description ? (
          <span className="mt-1 block text-xs leading-5 text-[var(--text-secondary)]">
            {item.description}
          </span>
        ) : null}
      </span>
      <button
        type="button"
        aria-label={`Dismiss ${config.label.toLowerCase()} notification`}
        onClick={() => onDismiss(item.id)}
        className="focus-ring -m-1 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[var(--text-secondary)] transition hover:bg-[color:var(--hover-surface)] hover:text-[var(--text-primary)]"
      >
        <X size={15} aria-hidden="true" />
      </button>
      {progressStyle ? (
        <span
          aria-hidden="true"
          className="absolute inset-x-0 bottom-0 h-0.5 bg-[color:var(--surface-container-high)]"
        >
          <span className="block h-full bg-[color:var(--accent-primary)] transition-[width] duration-normal ease-standard" style={progressStyle} />
        </span>
      ) : null}
    </article>
  );
}

export function ToastProvider({ children, maxVisible = TOAST_LIMIT, className = '' }) {
  const [toasts, setToasts] = useState(() => toast.getSnapshot());

  useEffect(() => toast.subscribe(setToasts), []);

  const visibleToasts = useMemo(
    () => toasts.slice(0, Math.max(1, maxVisible)),
    [maxVisible, toasts],
  );

  return (
    <>
      {children}
      <div
        className={joinClasses('visus-toast-viewport', className)}
        aria-label="Notifications"
      >
        {visibleToasts.map((item) => (
          <ToastItem key={item.id} item={item} onDismiss={toast.dismiss} />
        ))}
      </div>
    </>
  );
}

export default toast;
