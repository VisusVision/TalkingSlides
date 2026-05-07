import { useEffect, useMemo, useRef, useState } from 'react';
import { Focus, Layers3 } from 'lucide-react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  fetchCatalog,
  fetchLesson,
  fetchPlaybackToken,
  fetchProjectTranscript,
  saveProgress,
} from '../api';
import VideoStage from '../components/player/VideoStage';
import ChapterList from '../components/player/ChapterList';
import TranscriptPanel from '../components/player/TranscriptPanel';
import NotesPanel from '../components/player/NotesPanel';
import RelatedLessonsRow from '../components/player/RelatedLessonsRow';
import Button from '../components/ui/Button';
import SurfaceCard from '../components/ui/SurfaceCard';
import { normalizeLesson } from '../lib/content';
import { buildChapters, buildTranscriptLines } from '../lib/watch';

function normalizeCatalogList(payload) {
  const list = Array.isArray(payload) ? payload : payload.results || [];
  return list.map((item) => normalizeLesson(item));
}

function lessonSearchMatch(lesson, query) {
  const q = String(query || '').trim().toLowerCase();
  if (!q) return true;

  return [lesson.title, lesson.description, lesson.teacherName, lesson.categoryName]
    .join(' ')
    .toLowerCase()
    .includes(q);
}

function savedNoteKey(lessonId) {
  return `visus-notes-${lessonId || 'none'}`;
}

function draftNoteKey(lessonId) {
  return `visus-notes-draft-${lessonId || 'none'}`;
}

export default function Watch({ searchQuery, user, onLoginRequest }) {
  const navigate = useNavigate();
  const videoRef = useRef(null);
  const [searchParams, setSearchParams] = useSearchParams();

  const [catalogLessons, setCatalogLessons] = useState([]);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [loadingLesson, setLoadingLesson] = useState(true);
  const [lessonError, setLessonError] = useState('');

  const [lesson, setLesson] = useState(null);
  const [transcriptPayload, setTranscriptPayload] = useState(null);
  const [playbackTime, setPlaybackTime] = useState(0);
  const [focusMode, setFocusMode] = useState(false);
  const [notesCollapsed, setNotesCollapsed] = useState(false);
  const [transcriptCollapsed, setTranscriptCollapsed] = useState(false);
  const [chaptersCollapsed, setChaptersCollapsed] = useState(false);
  const [notes, setNotes] = useState('');
  const [savedNotes, setSavedNotes] = useState('');
  const [savedAtLabel, setSavedAtLabel] = useState('Auto-saved locally');
  const [saveHint, setSaveHint] = useState('');
  const progressSavedAtRef = useRef(0);

  const activeLessonId = Number(searchParams.get('lesson') || 0) || null;

  useEffect(() => {
    let active = true;

    async function loadCatalogLessons() {
      setLoadingCatalog(true);
      try {
        const payload = await fetchCatalog();
        if (!active) return;
        const list = normalizeCatalogList(payload);
        setCatalogLessons(list);

        if (!activeLessonId && list[0]?.id) {
          setSearchParams({ lesson: String(list[0].id) }, { replace: true });
        }
      } catch {
        if (!active) return;
        setCatalogLessons([]);
      } finally {
        if (active) {
          setLoadingCatalog(false);
        }
      }
    }

    loadCatalogLessons();

    return () => {
      active = false;
    };
  }, [activeLessonId, setSearchParams]);

  useEffect(() => {
    if (!activeLessonId) return;

    let active = true;

    async function loadLessonData() {
      setLoadingLesson(true);
      setLessonError('');

      try {
        const [lessonData, transcriptData] = await Promise.all([
          fetchLesson(activeLessonId),
          fetchProjectTranscript(activeLessonId).catch(() => null),
        ]);

        let playbackData = null;
        try {
          playbackData = await fetchPlaybackToken(activeLessonId);
        } catch (playbackError) {
          const protectionMode = String(lessonData?.protection?.mode || '').trim().toLowerCase();
          if (protectionMode && protectionMode !== 'public') {
            throw playbackError;
          }
        }

        if (!active) return;

        const integratedLesson = playbackData
          ? {
              ...lessonData,
              stream_url: playbackData.video_url || lessonData.stream_url,
              srt_url: playbackData.srt_url || lessonData.srt_url,
              vtt_url: playbackData.vtt_url || lessonData.vtt_url,
              subtitle_vtt_url: playbackData.subtitle_vtt_url || lessonData.subtitle_vtt_url,
              avatar_overlay: playbackData.avatar_overlay || lessonData.avatar_overlay,
              playback_status: playbackData.playback_status || lessonData.playback_status,
              protection: playbackData.protection || lessonData.protection,
              streaming: playbackData.streaming || lessonData.streaming,
              watermark: playbackData.watermark || lessonData.watermark,
            }
          : lessonData;

        setLesson(integratedLesson);
        setTranscriptPayload(transcriptData);
        setPlaybackTime(0);
        progressSavedAtRef.current = 0;
      } catch (err) {
        if (!active) return;
        setLessonError(err.message || 'Failed to load lesson.');
      } finally {
        if (active) {
          setLoadingLesson(false);
        }
      }
    }

    loadLessonData();

    return () => {
      active = false;
    };
  }, [activeLessonId]);

  useEffect(() => {
    const persisted = window.localStorage.getItem(savedNoteKey(activeLessonId)) || '';
    const draft = window.localStorage.getItem(draftNoteKey(activeLessonId));
    const hydrated = draft !== null ? draft : persisted;

    setNotes(hydrated || '');
    setSavedNotes(persisted || '');

    if (draft !== null && draft !== persisted) {
      setSavedAtLabel('Loaded local draft');
      setSaveHint('Unsaved note draft restored from this browser cache.');
      return;
    }

    setSavedAtLabel(persisted ? 'Loaded saved note' : 'Drafting locally');
    setSaveHint('');
  }, [activeLessonId]);

  useEffect(() => {
    const draftKey = draftNoteKey(activeLessonId);
    if (notes !== savedNotes) {
      window.localStorage.setItem(draftKey, notes);
      return;
    }

    window.localStorage.removeItem(draftKey);
  }, [activeLessonId, notes, savedNotes]);

  const hasUnsavedNotes = notes !== savedNotes;

  useEffect(() => {
    if (!hasUnsavedNotes) return undefined;

    const handleBeforeUnload = (event) => {
      event.preventDefault();
      event.returnValue = '';
    };

    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [hasUnsavedNotes]);

  const saveNotes = () => {
    if (!user) {
      window.localStorage.setItem(draftNoteKey(activeLessonId), notes);
      setSavedAtLabel('Draft kept locally');
      setSaveHint('Sign in to save this note to your account session.');

      if (typeof onLoginRequest === 'function') {
        onLoginRequest(activeLessonId ? `/watch?lesson=${activeLessonId}` : '/watch');
      }
      return;
    }

    window.localStorage.setItem(savedNoteKey(activeLessonId), notes);
    window.localStorage.removeItem(draftNoteKey(activeLessonId));
    setSavedNotes(notes);
    setSaveHint('');
    setSavedAtLabel(`Saved at ${new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}`);
  };

  const visibleLessons = useMemo(
    () => catalogLessons.filter((item) => lessonSearchMatch(item, searchQuery)),
    [catalogLessons, searchQuery],
  );

  const relatedLessons = useMemo(() => {
    const source = visibleLessons.length ? visibleLessons : catalogLessons;
    return source.filter((item) => item.id !== activeLessonId).slice(0, 12);
  }, [visibleLessons, catalogLessons, activeLessonId]);

  const chapters = useMemo(() => buildChapters(transcriptPayload, lesson), [transcriptPayload, lesson]);
  const transcriptLines = useMemo(
    () => buildTranscriptLines(transcriptPayload, lesson),
    [transcriptPayload, lesson],
  );

  const activeChapterId = useMemo(() => {
    const activeChapter = chapters.find(
      (chapter) => playbackTime >= chapter.startSeconds && playbackTime < chapter.endSeconds,
    );
    return activeChapter?.id || chapters[0]?.id || null;
  }, [chapters, playbackTime]);

  const jumpToTime = (seconds) => {
    if (videoRef.current) {
      videoRef.current.currentTime = Number(seconds || 0);
      videoRef.current.play().catch(() => {});
    }
    setPlaybackTime(Number(seconds || 0));
  };

  const handlePlaybackTimeChange = (seconds) => {
    const currentTime = Number(seconds || 0);
    setPlaybackTime(currentTime);

    if (!user || !activeLessonId || !videoRef.current?.duration) {
      return;
    }

    const percent = Math.round((currentTime / Number(videoRef.current.duration || 1)) * 100);
    if (Number.isNaN(percent)) {
      return;
    }

    if (Math.abs(percent - progressSavedAtRef.current) >= 5) {
      progressSavedAtRef.current = percent;
      saveProgress(activeLessonId, Math.max(0, Math.min(100, percent))).catch(() => {});
    }
  };

  return (
    <div className="space-y-5">
      <SurfaceCard className="token-glass flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="label-sm">Lecture Player</p>
          <h1 className="headline-md mt-1 text-[var(--text-primary)]">Study With Focused Context</h1>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2 rounded-full token-surface px-3 py-1.5 text-sm text-[var(--text-secondary)]">
            <Layers3 size={14} />
            <select
              value={activeLessonId || ''}
              onChange={(event) => {
                const nextId = Number(event.target.value || 0);
                if (!nextId) return;
                setSearchParams({ lesson: String(nextId) });
              }}
              className="max-w-[10.5rem] truncate border border-[var(--border-subtle)] bg-[var(--surface-elevated)] text-[var(--text-primary)] focus:outline-none sm:max-w-[15rem]"
            >
              {!activeLessonId && <option value="" className="bg-[var(--surface-elevated)] text-[var(--text-primary)]"
              >Select a lesson</option>}
              {(visibleLessons.length ? visibleLessons : catalogLessons).map((item) => (
                <option key={item.id} value={item.id}
                className="bg-[var(--surface-elevated)] text-[var(--text-primary)]"
                >
                  {item.title}
                </option>
              ))}
            </select>
          </label>

          <Button variant={focusMode ? 'primary' : 'secondary'} onClick={() => setFocusMode((prev) => !prev)}>
            <Focus size={15} />
            <span>{focusMode ? 'Exit Focus' : 'Focus Mode'}</span>
          </Button>
        </div>
      </SurfaceCard>

      {loadingCatalog && (
        <SurfaceCard elevated>
          <p className="body-md">Loading lesson catalog...</p>
        </SurfaceCard>
      )}

      {lessonError && (
        <SurfaceCard elevated>
          <p className="text-sm text-[color:var(--feedback-danger-fg)]">{lessonError}</p>
        </SurfaceCard>
      )}

      {!loadingCatalog && !activeLessonId && (
        <SurfaceCard elevated className="space-y-3">
          <p className="title-lg text-[var(--text-primary)]">No lesson selected</p>
          <Button onClick={() => navigate('/')}>
            <span>Go To Dashboard</span>
          </Button>
        </SurfaceCard>
      )}

      {activeLessonId && !lessonError && (
        <section className="layout-grid-12">
          <div className={`${focusMode ? 'lg:col-span-7' : 'lg:col-span-8'} space-y-5`}>
            {loadingLesson ? (
              <SurfaceCard elevated>
                <p className="body-md">Loading lesson player...</p>
              </SurfaceCard>
            ) : (
              <VideoStage
                lesson={lesson}
                onPlaybackTimeChange={handlePlaybackTimeChange}
                videoRef={videoRef}
              />
            )}

            {!focusMode && (
              <RelatedLessonsRow
                lessons={relatedLessons}
                onOpenLesson={(id) => setSearchParams({ lesson: String(id) })}
              />
            )}
          </div>

          <aside className={`${focusMode ? 'lg:col-span-5' : 'lg:col-span-4'} space-y-5`}>
            <NotesPanel
              notes={notes}
              onNotesChange={setNotes}
              onSave={saveNotes}
              savedAtLabel={savedAtLabel}
              unsaved={hasUnsavedNotes}
              saveActionLabel={user ? 'Save Note' : 'Sign In To Save'}
              saveHint={saveHint || (!user ? 'Drafts remain cached locally while you sign in.' : '')}
              collapsed={notesCollapsed}
              onToggle={() => setNotesCollapsed((prev) => !prev)}
            />
            <TranscriptPanel
              lines={transcriptLines}
              playbackTime={playbackTime}
              onJump={jumpToTime}
              collapsed={transcriptCollapsed}
              onToggle={() => setTranscriptCollapsed((prev) => !prev)}
            />
            <ChapterList
              chapters={chapters}
              activeChapterId={activeChapterId}
              onJump={jumpToTime}
              collapsed={chaptersCollapsed}
              onToggle={() => setChaptersCollapsed((prev) => !prev)}
            />
          </aside>
        </section>
      )}

      {focusMode && (
        <RelatedLessonsRow
          lessons={relatedLessons}
          onOpenLesson={(id) => setSearchParams({ lesson: String(id) })}
        />
      )}
    </div>
  );
}
