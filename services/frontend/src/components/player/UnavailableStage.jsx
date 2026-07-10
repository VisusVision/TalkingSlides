import { AlertTriangle, ShieldAlert } from 'lucide-react';
import SurfaceCard from '../ui/SurfaceCard';
import { PLAYER_MODES } from './playerMode';
import { useLocale } from '../../i18n/LocaleProvider';

function defaultMessageFor(mode, reason, t) {
  const cleanReason = String(reason || '').trim().toLowerCase();
  if (mode === PLAYER_MODES.DRM_SHAKA || cleanReason.startsWith('drm') || cleanReason === 'eme_unavailable') {
    return t('playbackProtectedUnavailable');
  }
  if (cleanReason.startsWith('secure_hls')) {
    return t('playbackSecureUnavailable');
  }
  return t('playbackVideoUnavailable');
}

export default function UnavailableStage({ message = '', reason = '', mode = PLAYER_MODES.UNAVAILABLE }) {
  const { t } = useLocale();
  const isProtected = mode === PLAYER_MODES.DRM_SHAKA
    || String(reason || '').startsWith('drm')
    || reason === 'eme_unavailable';
  const Icon = isProtected ? ShieldAlert : AlertTriangle;
  const displayMessage = String(message || '').trim() || defaultMessageFor(mode, reason, t);
  const reasonLabel = String(reason || '').trim().replace(/_/g, ' ');

  return (
    <SurfaceCard elevated className="space-y-4 p-4 sm:p-5">
      <div className="flex aspect-video items-center justify-center rounded-xl bg-[color:var(--video-stage-bg)] p-6 text-center text-[color:var(--media-text-on-image)]">
        <div className="max-w-xl space-y-3">
          <span className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-black/35">
            <Icon size={22} />
          </span>
          <p className="text-base font-semibold">{displayMessage}</p>
          {reasonLabel && (
            <p className="text-xs opacity-75">{t('playbackReason', { reason: reasonLabel })}</p>
          )}
        </div>
      </div>
    </SurfaceCard>
  );
}
