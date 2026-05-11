const POSITIONS = new Set(['top-right', 'top-left', 'bottom-right', 'bottom-left', 'custom']);
const SIZES = new Set(['small', 'medium', 'large']);
export const AVATAR_SIZE_WIDTHS = {
  small: 0.18,
  medium: 0.24,
  large: 0.30,
};

export const DEFAULT_AVATAR_PLACEMENT = {
  position: 'top-right',
  size: 'medium',
  x: 0.72,
  y: 0.08,
  width: 0.24,
};

const MARGIN_X = 0.04;
const MARGIN_Y = 0.08;
const HEIGHT_RATIO = 9 / 16;
const MIN_WIDTH = 0.12;
const MAX_WIDTH = 0.35;

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function numeric(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function ratio(value, fallback) {
  return numeric(value, fallback);
}

function percentRatio(value, fallback) {
  return numeric(value, fallback * 100) / 100;
}

function sizeFromWidth(width) {
  if (width <= 0.205) return 'small';
  if (width >= 0.27) return 'large';
  return 'medium';
}

function positionedCoordinates(position, width) {
  const height = width * HEIGHT_RATIO;
  if (position === 'top-left') return { x: MARGIN_X, y: MARGIN_Y };
  if (position === 'bottom-left') return { x: MARGIN_X, y: 1 - height - MARGIN_Y };
  if (position === 'bottom-right') return { x: 1 - width - MARGIN_X, y: 1 - height - MARGIN_Y };
  return { x: 1 - width - MARGIN_X, y: MARGIN_Y };
}

export function normalizeAvatarPlacement(raw = null, fallback = DEFAULT_AVATAR_PLACEMENT) {
  const source = raw?.avatar_placement || raw?.placement || raw?.defaults || raw || {};
  const base = {
    ...DEFAULT_AVATAR_PLACEMENT,
    ...(fallback || {}),
  };
  let position = String(source.position || source.anchor || base.position || 'top-right').trim().toLowerCase();
  if (!POSITIONS.has(position)) position = base.position || 'top-right';

  let size = String(source.size || base.size || 'medium').trim().toLowerCase();
  if (!SIZES.has(size)) size = base.size || 'medium';

  const defaultWidth = AVATAR_SIZE_WIDTHS[size] || AVATAR_SIZE_WIDTHS.medium;
  const width = clamp(
    Object.prototype.hasOwnProperty.call(source, 'width')
      ? ratio(source.width, defaultWidth)
      : percentRatio(source.width_percent, defaultWidth),
    MIN_WIDTH,
    MAX_WIDTH,
  );
  size = sizeFromWidth(width);

  let x;
  let y;
  if (position === 'custom') {
    x = Object.prototype.hasOwnProperty.call(source, 'x')
      ? ratio(source.x, base.x)
      : percentRatio(source.x_percent, base.x);
    y = Object.prototype.hasOwnProperty.call(source, 'y')
      ? ratio(source.y, base.y)
      : percentRatio(source.y_percent, base.y);
  } else {
    ({ x, y } = positionedCoordinates(position, width));
  }

  const height = width * HEIGHT_RATIO;
  return {
    position,
    size,
    x: Number(clamp(x, 0, Math.max(0, 1 - width)).toFixed(4)),
    y: Number(clamp(y, 0, Math.max(0, 1 - height)).toFixed(4)),
    width: Number(width.toFixed(4)),
  };
}

export function avatarPlacementStyle(raw = null) {
  const placement = normalizeAvatarPlacement(raw);
  const width = `${(placement.width * 100).toFixed(2)}%`;
  const base = {
    width,
    maxWidth: 'calc(100% - 1rem)',
  };

  if (placement.position === 'custom') {
    return {
      ...base,
      left: `${(placement.x * 100).toFixed(2)}%`,
      top: `${(placement.y * 100).toFixed(2)}%`,
    };
  }
  if (placement.position === 'top-left') {
    return { ...base, left: '4%', top: '8%' };
  }
  if (placement.position === 'bottom-left') {
    return { ...base, left: '4%', bottom: '8%' };
  }
  if (placement.position === 'bottom-right') {
    return { ...base, right: '4%', bottom: '8%' };
  }
  return { ...base, right: '4%', top: '8%' };
}

export const AVATAR_PLACEMENT_OPTIONS = [
  { value: 'top-right', label: 'Top right' },
  { value: 'top-left', label: 'Top left' },
  { value: 'bottom-right', label: 'Bottom right' },
  { value: 'bottom-left', label: 'Bottom left' },
  { value: 'custom', label: 'Custom' },
];

export const AVATAR_SIZE_OPTIONS = [
  { value: 'small', label: 'Small' },
  { value: 'medium', label: 'Medium' },
  { value: 'large', label: 'Large' },
];
