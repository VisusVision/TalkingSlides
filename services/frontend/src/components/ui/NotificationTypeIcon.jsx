import {
  AlertTriangle,
  Bell,
  CheckCircle2,
  MessageSquare,
  PlayCircle,
  UserCircle,
} from 'lucide-react';

const TYPE_STYLES = {
  publisher_comment_on_lesson: {
    Icon: MessageSquare,
    tone: 'bg-[color:var(--status-info-bg)] text-[color:var(--status-info-fg)] ring-[color:color-mix(in_srgb,var(--status-info-fg),transparent_68%)]',
  },
  student_followed_publisher_new_lesson: {
    Icon: PlayCircle,
    tone: 'bg-[color:var(--hover-accent-soft)] text-[var(--accent-primary)] ring-[color:color-mix(in_srgb,var(--accent-primary),transparent_68%)]',
  },
  publisher_lesson_render_done: {
    Icon: CheckCircle2,
    tone: 'bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)] ring-[color:color-mix(in_srgb,var(--status-success-fg),transparent_68%)]',
  },
  publisher_lesson_render_failed: {
    Icon: AlertTriangle,
    tone: 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)] ring-[color:color-mix(in_srgb,var(--status-warning-fg),transparent_64%)]',
  },
  publisher_avatar_render_done: {
    Icon: UserCircle,
    tone: 'bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)] ring-[color:color-mix(in_srgb,var(--status-success-fg),transparent_68%)]',
  },
  publisher_avatar_render_failed: {
    Icon: UserCircle,
    BadgeIcon: AlertTriangle,
    tone: 'bg-[color:var(--status-danger-bg)] text-[color:var(--status-danger-fg)] ring-[color:color-mix(in_srgb,var(--status-danger-fg),transparent_64%)]',
  },
};

const DEFAULT_STYLE = {
  Icon: Bell,
  tone: 'bg-[var(--surface-container-highest)] text-[var(--text-secondary)] ring-[color:var(--border-subtle)]',
};

const SIZES = {
  sm: {
    wrapper: 'h-8 w-8',
    icon: 16,
    badge: 'h-4 w-4',
    badgeIcon: 9,
  },
  md: {
    wrapper: 'h-10 w-10',
    icon: 18,
    badge: 'h-5 w-5',
    badgeIcon: 10,
  },
};

export default function NotificationTypeIcon({ eventType, size = 'md', className = '' }) {
  const meta = TYPE_STYLES[eventType] || DEFAULT_STYLE;
  const dimensions = SIZES[size] || SIZES.md;
  const { Icon, BadgeIcon } = meta;

  return (
    <span
      aria-hidden="true"
      className={`relative inline-flex shrink-0 items-center justify-center rounded-full ring-1 ${dimensions.wrapper} ${meta.tone} ${className}`}
    >
      <Icon size={dimensions.icon} strokeWidth={2.1} />
      {BadgeIcon ? (
        <span className={`absolute -bottom-0.5 -right-0.5 inline-flex items-center justify-center rounded-full bg-[color:var(--status-danger-bg)] text-[color:var(--status-danger-fg)] ring-1 ring-[color:color-mix(in_srgb,var(--status-danger-fg),transparent_58%)] ${dimensions.badge}`}>
          <BadgeIcon size={dimensions.badgeIcon} strokeWidth={2.4} />
        </span>
      ) : null}
    </span>
  );
}
