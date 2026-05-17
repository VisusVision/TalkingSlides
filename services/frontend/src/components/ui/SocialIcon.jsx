const ICONS = {
  x: (
    <path
      fill="currentColor"
      d="M13.6 10.2 19.3 3.5h-1.4L13 9.2 9.1 3.5H4.5l6 8.8-6 7.2h1.4l5.2-6.2 4.2 6.2h4.6l-6.3-9.3Zm-1.8 2.2-.6-.9-4.9-6.9h2.1l3.8 5.4.6.9 5.2 7.4h-2.1l-4.1-5.9Z"
    />
  ),
  instagram: (
    <>
      <rect x="4.2" y="4.2" width="15.6" height="15.6" rx="4.4" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <circle cx="12" cy="12" r="3.6" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <circle cx="16.7" cy="7.3" r="1.1" fill="currentColor" />
    </>
  ),
  youtube: (
    <path
      fill="currentColor"
      d="M21.3 7.2a3 3 0 0 0-2.1-2.1C17.3 4.6 12 4.6 12 4.6s-5.3 0-7.2.5a3 3 0 0 0-2.1 2.1A31 31 0 0 0 2.2 12c0 1.7.2 3.4.5 4.8a3 3 0 0 0 2.1 2.1c1.9.5 7.2.5 7.2.5s5.3 0 7.2-.5a3 3 0 0 0 2.1-2.1c.3-1.4.5-3.1.5-4.8s-.2-3.4-.5-4.8ZM10 15.5v-7l6 3.5-6 3.5Z"
    />
  ),
  linkedin: (
    <path
      fill="currentColor"
      d="M6.6 19.8H3.3V8.8h3.3v11ZM4.9 7.3a1.9 1.9 0 1 1 0-3.8 1.9 1.9 0 0 1 0 3.8Zm15.6 12.5h-3.3v-5.4c0-1.3 0-2.9-1.8-2.9s-2.1 1.4-2.1 2.8v5.5H10V8.8h3.1v1.5h.1a3.4 3.4 0 0 1 3.1-1.7c3.4 0 4 2.2 4 5.1v6.1h.2Z"
    />
  ),
  github: (
    <path
      fill="currentColor"
      d="M12 2.5a9.8 9.8 0 0 0-3.1 19.1c.5.1.7-.2.7-.5v-1.8c-2.9.6-3.5-1.2-3.5-1.2-.5-1.2-1.1-1.5-1.1-1.5-.9-.6.1-.6.1-.6 1 .1 1.5 1 1.5 1 .9 1.5 2.3 1.1 2.9.8.1-.7.3-1.1.6-1.3-2.3-.3-4.7-1.2-4.7-5.1 0-1.1.4-2.1 1-2.8-.1-.3-.4-1.3.1-2.8 0 0 .8-.3 2.8 1a9.6 9.6 0 0 1 5 0c1.9-1.3 2.8-1 2.8-1 .5 1.5.2 2.5.1 2.8.6.7 1 1.7 1 2.8 0 4-2.4 4.8-4.7 5.1.4.3.7 1 .7 2v2.9c0 .3.2.6.8.5A9.8 9.8 0 0 0 12 2.5Z"
    />
  ),
  facebook: (
    <path
      fill="currentColor"
      d="M13.6 21v-8h2.7l.4-3.1h-3.1V7.8c0-.9.3-1.5 1.6-1.5h1.7V3.5c-.3 0-1.4-.1-2.6-.1-2.6 0-4.4 1.6-4.4 4.5v2H7v3.1h2.9v8h3.7Z"
    />
  ),
  website: (
    <>
      <circle cx="12" cy="12" r="8.4" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" d="M3.8 12h16.4M12 3.6c2 2.3 3 5.1 3 8.4s-1 6.1-3 8.4c-2-2.3-3-5.1-3-8.4s1-6.1 3-8.4Z" />
    </>
  ),
  contact: (
    <>
      <rect x="3.7" y="5.8" width="16.6" height="12.4" rx="2.2" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" d="m4.5 7 7.5 5.8L19.5 7" />
    </>
  ),
};

export default function SocialIcon({ type = 'website', size = 16, className = '' }) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      className={className}
    >
      {ICONS[type] || ICONS.website}
    </svg>
  );
}
