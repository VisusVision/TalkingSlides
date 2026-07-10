import Button from '../ui/Button';
import { AVATAR_OVERLAY_Z_INDEX } from './AvatarOverlayLayer';
import { useLocale } from '../../i18n/LocaleProvider';

export default function ContinueNextPrompt({ prompt, onContinue, onCancel }) {
  const { t } = useLocale();
  const lesson = prompt?.lesson;
  if (!lesson) return null;

  const secondsRemaining = Math.max(0, Number(prompt?.secondsRemaining || 0));
  const title = String(lesson.title || t('nextLessonFallback')).trim() || t('nextLessonFallback');

  return (
    <div
      data-testid="watch-autoplay-next"
      aria-live="polite"
      className="pointer-events-none absolute inset-x-3 bottom-16 flex justify-center px-2 sm:bottom-20"
      style={{ zIndex: AVATAR_OVERLAY_Z_INDEX.captions + 20 }}
    >
      <div className="pointer-events-auto w-full max-w-lg rounded-xl border border-white/15 bg-[color:rgba(8,12,20,0.9)] p-3 text-white shadow-2xl backdrop-blur sm:p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-white/65">{t('upNext')}</p>
            <h2 className="mt-1 line-clamp-2 text-lg font-semibold leading-tight">
              {t('nextLessonTitle', { title })}
            </h2>
            <p className="mt-1 text-sm text-white/75">
              {t(secondsRemaining === 1 ? 'continuingInSecond' : 'continuingInSeconds', { count: secondsRemaining })}
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <Button size="sm" onClick={onContinue} className="bg-white text-slate-950 hover:scale-100 hover:bg-white/90">
              {t('continueNow')}
            </Button>
            <Button size="sm" variant="secondary" onClick={onCancel} className="bg-white/10 text-white hover:bg-white/20">
              {t('stayHere')}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
