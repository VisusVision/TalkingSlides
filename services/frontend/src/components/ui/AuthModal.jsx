import { useEffect, useState } from 'react';
import { LogIn, X } from 'lucide-react';
import {
  fetchAuthProviders,
  login,
  startGoogleRedirectFlow,
} from '../../api';
import Button from './Button';
import SurfaceCard from './SurfaceCard';
import { useI18n } from '../../i18n/I18nProvider';

export default function AuthModal({ open, onClose, onLoginSuccess }) {
  const { t } = useI18n();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [providerConfig, setProviderConfig] = useState(null);

  useEffect(() => {
    if (!open) return;
    fetchAuthProviders()
      .then((data) => setProviderConfig(data))
      .catch(() => setProviderConfig({ google: { enabled: false, redirect_flow_enabled: false } }));
  }, [open]);

  if (!open) {
    return null;
  }

  const canUseGoogle =
    Boolean(providerConfig?.google?.enabled) &&
    Boolean(providerConfig?.google?.redirect_flow_enabled);

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError('');
    setLoading(true);

    try {
      const data = await login(username, password);
      onLoginSuccess(data.user);
      setUsername('');
      setPassword('');
    } catch (err) {
      setError(err.message || t('auth.signInFailed'));
    } finally {
      setLoading(false);
    }
  };

  const handleGoogleRedirect = async () => {
    try {
      const data = await startGoogleRedirectFlow();
      if (!data?.authorization_url) {
        throw new Error(t('auth.googleUnavailable'));
      }
      window.location.href = data.authorization_url;
    } catch (err) {
      setError(err.message || t('auth.googleFailed'));
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[color:var(--modal-backdrop)] p-4 backdrop-blur-sm">
      <SurfaceCard className="relative w-full max-w-md" elevated>
        <button
          type="button"
          onClick={onClose}
          className="focus-ring absolute right-4 top-4 inline-flex h-8 w-8 items-center justify-center rounded-full text-[var(--text-secondary)] hover:bg-[color:var(--surface-muted)]"
          aria-label={t('auth.closeSignIn')}
        >
          <X size={16} />
        </button>

        <p className="label-sm">{t('auth.welcomeBack')}</p>
        <h2 className="headline-md mt-2 text-[var(--text-primary)]">{t('auth.continueLearning')}</h2>
        <p className="body-md mt-2">{t('auth.intro')}</p>

        {canUseGoogle && (
          <Button
            className="mt-5 w-full"
            variant="secondary"
            onClick={handleGoogleRedirect}
          >
            <svg
              className="h-4 w-4"
              viewBox="0 0 533.5 544.3"
              xmlns="http://www.w3.org/2000/svg"
              aria-hidden="true"
            >
              <path fill="#4285F4" d="M533.5 278.4c0-17.6-1.6-35.2-4.8-52.0H272v98.5h146.9c-6.4 34.4-22.4 63.5-47.7 83.1v68.9h77.1c45.1-41.6 71.2-102.8 71.2-198.5z" />
              <path fill="#34A853" d="M272 544.3c64.8 0 119.3-21.4 159.1-58.1l-77.1-68.9c-21.4 14.4-48.8 22.9-82 22.9-63 0-116.3-42.6-135.4-100.1H55.3v62.8C95.1 479.5 176.4 544.3 272 544.3z" />
              <path fill="#FBBC04" d="M136.6 331.1c-9.3-27.6-9.3-57.1 0-84.7V183.6H55.3c-41.9 84.7-41.9 184.0 0 268.7L136.6 331.1z" />
              <path fill="#EA4335" d="M272 107.7c36.8 0 70 12.9 96.2 34.4l72.1-72.1C391.4 26 334.8 0 272 0 176.4 0 95.1 64.8 55.3 156.3L136.6 219c19.1-57.5 72.4-100.1 135.4-100.1z" />
            </svg>
            <span>{t('auth.continueWithGoogle')}</span>
          </Button>
        )}

        <form className="mt-5 space-y-3" onSubmit={handleSubmit}>
          <label className="block text-sm text-[var(--text-secondary)]">
            {t('auth.username')}
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              className="focus-ring mt-1 h-11 w-full rounded-2xl border border-[var(--border-subtle)] bg-[color:var(--surface-muted)] px-3 text-[var(--text-primary)]"
              type="text"
              required
            />
          </label>

          <label className="block text-sm text-[var(--text-secondary)]">
            {t('auth.password')}
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="focus-ring mt-1 h-11 w-full rounded-2xl border border-[var(--border-subtle)] bg-[color:var(--surface-muted)] px-3 text-[var(--text-primary)]"
              type="password"
              required
            />
          </label>

          {error && (
            <p className="rounded-2xl bg-[color:var(--feedback-danger-bg)] px-3 py-2 text-sm text-[color:var(--feedback-danger-fg)]">{error}</p>
          )}

          <Button className="w-full" type="submit" disabled={loading}>
            <LogIn size={16} />
            <span>{loading ? t('auth.signingIn') : t('auth.signIn')}</span>
          </Button>
        </form>
      </SurfaceCard>
    </div>
  );
}
