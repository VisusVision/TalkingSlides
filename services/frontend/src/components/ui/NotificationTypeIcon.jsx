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
    tone: 'bg-[color:rgba(59,130,246,0.12)] text-blue-700 ring-blue-200 dark:bg-[color:rgba(96,165,250,0.16)] dark:text-blue-200 dark:ring-blue-400/30',
  },
  student_followed_publisher_new_lesson: {
    Icon: PlayCircle,
    tone: 'bg-[color:rgba(99,102,241,0.13)] text-indigo-700 ring-indigo-200 dark:bg-[color:rgba(129,140,248,0.18)] dark:text-indigo-200 dark:ring-indigo-400/30',
  },
  publisher_lesson_render_done: {
    Icon: CheckCircle2,
    tone: 'bg-[color:rgba(16,185,129,0.13)] text-emerald-700 ring-emerald-200 dark:bg-[color:rgba(52,211,153,0.16)] dark:text-emerald-200 dark:ring-emerald-400/30',
  },
  publisher_lesson_render_failed: {
    Icon: AlertTriangle,
    tone: 'bg-[color:rgba(245,158,11,0.16)] text-amber-700 ring-amber-200 dark:bg-[color:rgba(251,191,36,0.18)] dark:text-amber-200 dark:ring-amber-400/30',
  },
  publisher_avatar_render_done: {
    Icon: UserCircle,
    tone: 'bg-[color:rgba(20,184,166,0.13)] text-teal-700 ring-teal-200 dark:bg-[color:rgba(45,212,191,0.16)] dark:text-teal-200 dark:ring-teal-400/30',
  },
  publisher_avatar_render_failed: {
    Icon: UserCircle,
    BadgeIcon: AlertTriangle,
    tone: 'bg-[color:rgba(239,68,68,0.12)] text-red-700 ring-red-200 dark:bg-[color:rgba(248,113,113,0.16)] dark:text-red-200 dark:ring-red-400/30',
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
        <span className={`absolute -bottom-0.5 -right-0.5 inline-flex items-center justify-center rounded-full bg-[var(--surface-container-high)] text-red-700 ring-1 ring-red-200 dark:bg-[var(--surface-container-highest)] dark:text-red-200 dark:ring-red-400/40 ${dimensions.badge}`}>
          <BadgeIcon size={dimensions.badgeIcon} strokeWidth={2.4} />
        </span>
      ) : null}
    </span>
  );
}
