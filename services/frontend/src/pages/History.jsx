import { useEffect, useMemo, useState } from 'react';
import { BookOpenText } from 'lucide-react';
import { fetchUserHistory } from '../api';
import LearningLessonCard, { normalizeLearningRows } from '../components/library/LearningLessonCard';
import SurfaceCard from '../components/ui/SurfaceCard';
import { useI18n } from '../i18n/I18nProvider';

export default function History() {
  const { t } = useI18n();
  const [historyRows, setHistoryRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;

    async function loadHistory() {
      setLoading(true);
      setError('');
      try {
        const payload = await fetchUserHistory();
        if (!active) return;
        setHistoryRows(normalizeLearningRows(payload, 'history'));
      } catch (historyError) {
        if (!active) return;
        setError(historyError.message || t('history.loadError'));
        setHistoryRows([]);
      } finally {
        if (active) setLoading(false);
      }
    }

    loadHistory();
    return () => {
      active = false;
    };
  }, [t]);

  const rows = useMemo(
    () => [...historyRows].sort((a, b) => new Date(b.timestamp || 0) - new Date(a.timestamp || 0)),
    [historyRows],
  );

  return (
    <div className="space-y-6">
      <section>
        <p className="label-sm">{t('history.title')}</p>
        <h1 className="headline-md text-[var(--text-primary)]">{t('history.continueWatching')}</h1>
        <p className="body-md mt-2 max-w-2xl">{t('history.subtitle')}</p>
      </section>

      {loading ? (
        <SurfaceCard elevated>
          <p className="body-md">{t('history.loading')}</p>
        </SurfaceCard>
      ) : error ? (
        <SurfaceCard elevated>
          <p className="text-sm font-medium text-[color:var(--feedback-danger-fg)]">{error}</p>
        </SurfaceCard>
      ) : rows.length === 0 ? (
        <SurfaceCard elevated className="text-center">
          <BookOpenText className="mx-auto text-[var(--text-secondary)]" size={21} />
          <p className="title-lg mt-2 text-[var(--text-primary)]">{t('history.empty')}</p>
        </SurfaceCard>
      ) : (
        <div className="grid gap-3">
          {rows.map((item) => (
            <LearningLessonCard key={item.id} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}
