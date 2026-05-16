import { useEffect, useMemo, useState } from 'react';
import { Building2, CircleHelp, ExternalLink, Mail, Phone } from 'lucide-react';
import SurfaceCard from '../components/ui/SurfaceCard';
import { fetchHelpContent } from '../api';

const FALLBACK_HELP = {
  title: 'Help and Support',
  body:
    'Use Studio with a publisher account to create lessons, then use Watch for transcript-first study and local notes. Contact support if you need account, publishing, or playback assistance.',
  contact_email: '',
  contact_phone: '',
  company_name: '',
  company_address: '',
  support_url: '',
  is_default: true,
};

function safeUrl(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  if (/^https?:\/\//i.test(raw)) return raw;
  return '';
}

export default function Help() {
  const [content, setContent] = useState(FALLBACK_HELP);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchHelpContent()
      .then((payload) => {
        if (!active) return;
        const title = String(payload?.title || '').trim();
        const body = String(payload?.body || '').trim();
        setContent(title && body ? payload : FALLBACK_HELP);
        setFailed(false);
      })
      .catch(() => {
        if (!active) return;
        setContent(FALLBACK_HELP);
        setFailed(true);
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  const supportUrl = useMemo(() => safeUrl(content.support_url), [content.support_url]);
  const hasCompanyInfo = Boolean(content.company_name || content.company_address);
  const hasContactInfo = Boolean(content.contact_email || content.contact_phone || supportUrl || hasCompanyInfo);

  return (
    <div className="space-y-5">
      <section className="layout-grid-12">
        <SurfaceCard elevated className="lg:col-span-8">
          <div className="inline-flex items-center gap-2">
            <CircleHelp size={16} className="text-[var(--accent-primary)]" />
            <p className="label-sm">Help</p>
          </div>
          <h1 className="headline-md mt-2 text-[var(--text-primary)]">{content.title || FALLBACK_HELP.title}</h1>
          <p className="body-md mt-3 whitespace-pre-line">{content.body || FALLBACK_HELP.body}</p>
          {failed && (
            <p className="mt-3 rounded-xl bg-[var(--status-warning-bg)] px-3 py-2 text-sm text-[var(--status-warning-fg)]">
              Help content is temporarily unavailable, so default guidance is shown.
            </p>
          )}
        </SurfaceCard>

        <SurfaceCard className="lg:col-span-4">
          <p className="label-sm">Support</p>
          <p className="mt-2 text-sm text-[var(--text-secondary)]">
            {loading ? 'Loading support details...' : 'Use the available support details for account, publishing, or playback questions.'}
          </p>
        </SurfaceCard>
      </section>

      {hasContactInfo && (
        <section className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          {content.contact_email && (
            <SurfaceCard>
              <Mail size={17} className="text-[var(--accent-primary)]" />
              <p className="label-sm mt-3">Email</p>
              <a className="focus-ring mt-2 inline-block break-words text-sm font-semibold text-[var(--text-primary)]" href={`mailto:${content.contact_email}`}>
                {content.contact_email}
              </a>
            </SurfaceCard>
          )}

          {content.contact_phone && (
            <SurfaceCard>
              <Phone size={17} className="text-[var(--accent-primary)]" />
              <p className="label-sm mt-3">Phone</p>
              <p className="mt-2 text-sm font-semibold text-[var(--text-primary)]">{content.contact_phone}</p>
            </SurfaceCard>
          )}

          {supportUrl && (
            <SurfaceCard>
              <ExternalLink size={17} className="text-[var(--accent-primary)]" />
              <p className="label-sm mt-3">Support URL</p>
              <a
                className="focus-ring mt-2 inline-flex items-center gap-1 break-all text-sm font-semibold text-[var(--text-primary)]"
                href={supportUrl}
                target="_blank"
                rel="noreferrer"
              >
                <span>{supportUrl}</span>
              </a>
            </SurfaceCard>
          )}

          {hasCompanyInfo && (
            <SurfaceCard>
              <Building2 size={17} className="text-[var(--accent-primary)]" />
              <p className="label-sm mt-3">Company</p>
              {content.company_name && (
                <p className="mt-2 text-sm font-semibold text-[var(--text-primary)]">{content.company_name}</p>
              )}
              {content.company_address && (
                <p className="mt-1 whitespace-pre-line text-sm text-[var(--text-secondary)]">{content.company_address}</p>
              )}
            </SurfaceCard>
          )}
        </section>
      )}
    </div>
  );
}
