import { useEffect, useRef, useState } from 'react';
import {
  Check,
  CloudUpload,
  LoaderCircle,
  Mic,
  MoreHorizontal,
  Pause,
  Play,
  Square,
  Upload,
  X,
} from 'lucide-react';
import { fetchVoiceSample, uploadVoiceSample } from '../../api';
import Button from '../ui/Button';

function ModalTab({ active, icon: Icon, children, onClick }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`focus-ring flex h-11 flex-1 items-center justify-center gap-2 rounded-full text-sm font-semibold transition ${
        active
          ? 'bg-[var(--surface-container-lowest)] text-[var(--text-primary)] shadow-sm'
          : 'text-[var(--outline)] hover:text-[var(--text-primary)]'
      }`}
    >
      <Icon size={16} />
      {children}
    </button>
  );
}

function VoiceCloneModal({ open, userId, onClose, onUploaded }) {
  const fileInputRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const mediaStreamRef = useRef(null);
  const recordedChunksRef = useRef([]);
  const timerRef = useRef(null);
  const [mode, setMode] = useState('record');
  const [file, setFile] = useState(null);
  const [recording, setRecording] = useState(false);
  const [recordedFile, setRecordedFile] = useState(null);
  const [recordedPreviewUrl, setRecordedPreviewUrl] = useState('');
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  const stopStream = () => {
    if (timerRef.current) window.clearInterval(timerRef.current);
    timerRef.current = null;
    mediaStreamRef.current?.getTracks?.().forEach((track) => track.stop());
    mediaStreamRef.current = null;
  };

  useEffect(() => () => stopStream(), []);

  useEffect(() => {
    if (!recordedFile) {
      setRecordedPreviewUrl('');
      return undefined;
    }
    const objectUrl = URL.createObjectURL(recordedFile);
    setRecordedPreviewUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [recordedFile]);

  useEffect(() => {
    if (!open) {
      stopStream();
      setRecording(false);
      setError('');
      setMessage('');
    }
  }, [open]);

  const selectFile = (nextFile) => {
    setError('');
    setMessage('');
    if (!nextFile) {
      setFile(null);
      return;
    }
    if (!String(nextFile.type || '').startsWith('audio/')) {
      setError('Lütfen geçerli bir ses dosyası seçin.');
      return;
    }
    if (nextFile.size > 25 * 1024 * 1024) {
      setError('Ses dosyası en fazla 25 MB olabilir.');
      return;
    }
    setFile(nextFile);
  };

  const startRecording = async () => {
    setError('');
    setMessage('');
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setError('Bu tarayıcı mikrofon kaydını desteklemiyor. Sesi yükle sekmesini kullanabilirsiniz.');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      recordedChunksRef.current = [];
      mediaStreamRef.current = stream;
      mediaRecorderRef.current = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data?.size) recordedChunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        const type = recorder.mimeType || 'audio/webm';
        const blob = new Blob(recordedChunksRef.current, { type });
        setRecordedFile(new File([blob], 'ses-klonu-kaydi.webm', { type }));
        setRecording(false);
        stopStream();
      };
      recorder.onerror = () => {
        setError('Ses kaydı tamamlanamadı. Lütfen tekrar deneyin.');
        setRecording(false);
        stopStream();
      };
      recorder.start();
      setRecordedFile(null);
      setRecordingSeconds(0);
      setRecording(true);
      timerRef.current = window.setInterval(() => setRecordingSeconds((seconds) => seconds + 1), 1000);
    } catch (recordError) {
      setError('Mikrofona erişilemedi. Tarayıcı iznini açın veya bir ses dosyası yükleyin.');
      stopStream();
    }
  };

  const stopRecording = () => {
    const recorder = mediaRecorderRef.current;
    if (recorder?.state === 'recording') recorder.stop();
  };

  const submit = async () => {
    const selectedFile = mode === 'upload' ? file : recordedFile;
    if (!selectedFile) {
      setError(mode === 'upload' ? 'Yüklemek için bir ses dosyası seçin.' : 'Önce en az 10 saniyelik bir ses kaydı oluşturun.');
      return;
    }
    if (mode === 'record' && recordingSeconds < 10) {
      setError('Ses klonu için en az 10 saniyelik kayıt gerekir.');
      return;
    }
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await uploadVoiceSample(userId, selectedFile);
      setMessage('Sesiniz başarıyla yüklendi ve klonlama için hazır.');
      await onUploaded?.();
    } catch (uploadError) {
      setError(uploadError?.message || 'Ses yüklenemedi. Lütfen tekrar deneyin.');
    } finally {
      setBusy(false);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-[var(--modal-backdrop)] p-3 backdrop-blur-sm" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section role="dialog" aria-modal="true" aria-labelledby="voice-clone-title" className="max-h-[calc(100vh-1.5rem)] w-full max-w-[44rem] overflow-y-auto rounded-[1.4rem] border border-[var(--border-subtle)] bg-[var(--surface)] p-5 shadow-2xl sm:p-6">
        <header className="flex items-start justify-between gap-4">
          <div>
            <h2 id="voice-clone-title" className="font-['Manrope'] text-xl font-bold text-[var(--text-primary)]">Ses klonu oluştur</h2>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-[var(--text-secondary)]">Sessiz bir ortamda, doğal ve net bir sesle konuşun. En az 10 saniyelik temiz bir örnek kullanın.</p>
          </div>
          <button type="button" onClick={onClose} className="focus-ring inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[var(--outline)] transition hover:bg-[var(--surface-container-high)] hover:text-[var(--text-primary)]" aria-label="Kapat">
            <X size={18} />
          </button>
        </header>

        <div className="mt-4 flex rounded-full bg-[var(--surface-container-high)] p-1" role="tablist" aria-label="Ses klonlama yöntemi">
          <ModalTab active={mode === 'record'} icon={Mic} onClick={() => setMode('record')}>Ses kaydet</ModalTab>
          <ModalTab active={mode === 'upload'} icon={CloudUpload} onClick={() => setMode('upload')}>Sesi yükle</ModalTab>
        </div>

        {mode === 'record' ? (
          <div className="mt-6 rounded-[1.25rem] bg-[var(--surface-container-low)] p-6 sm:p-8">
            <div className="mx-auto flex min-h-64 max-w-xl flex-col items-center justify-center rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-container-lowest)] px-5 text-center">
              <span className={`inline-flex h-16 w-16 items-center justify-center rounded-full ${recording ? 'animate-pulse bg-red-500/15 text-red-500' : 'bg-[var(--hover-accent-soft)] text-[var(--accent-primary)]'}`}>
                <Mic size={27} />
              </span>
              <p className="mt-5 text-base font-bold text-[var(--text-primary)]">{recording ? 'Sesiniz kaydediliyor' : recordedFile ? 'Kaydınız hazır' : 'Mikrofonla ses örneği kaydedin'}</p>
              <p className="mt-2 text-sm text-[var(--text-secondary)]">{recording ? `${recordingSeconds} sn · Net ve doğal bir şekilde konuşmaya devam edin.` : 'En iyi sonuç için yankısız ve sessiz bir ortam seçin.'}</p>
              <button
                type="button"
                onClick={recording ? stopRecording : startRecording}
                className={`focus-ring mt-6 inline-flex h-11 items-center gap-2 rounded-full px-5 text-sm font-bold transition ${recording ? 'bg-red-500 text-white hover:bg-red-600' : 'bg-[var(--accent-primary)] text-[var(--accent-inverse)] hover:brightness-110'}`}
              >
                {recording ? <Square size={15} fill="currentColor" /> : <Mic size={16} />}
                {recording ? 'Kaydı bitir' : recordedFile ? 'Yeniden kaydet' : 'Kaydı başlat'}
              </button>
              {recordedPreviewUrl ? <audio className="mt-5 w-full" controls src={recordedPreviewUrl} /> : null}
            </div>
          </div>
        ) : (
          <div className="mt-6 rounded-[1.25rem] bg-[var(--surface-container-low)] p-6 sm:p-8">
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                selectFile(event.dataTransfer.files?.[0] || null);
              }}
              className="focus-ring flex min-h-64 w-full flex-col items-center justify-center rounded-2xl border border-dashed border-[var(--outline-variant)] bg-[var(--surface-container-lowest)] px-5 text-center transition hover:border-[var(--accent-primary)] hover:bg-[var(--hover-accent-soft)]"
            >
              <span className="inline-flex h-16 w-16 items-center justify-center rounded-full bg-[var(--hover-accent-soft)] text-[var(--accent-primary)]"><Upload size={27} /></span>
              <span className="mt-5 text-base font-bold text-[var(--text-primary)]">{file ? file.name : 'Ses dosyanızı buraya bırakın'}</span>
              <span className="mt-2 text-sm text-[var(--text-secondary)]">veya bilgisayarınızdan seçmek için tıklayın</span>
              <span className="mt-4 rounded-full bg-[var(--surface-container-high)] px-3 py-1.5 text-xs font-semibold text-[var(--outline)]">MP3, WAV, M4A veya WEBM · En fazla 25 MB</span>
              <input ref={fileInputRef} type="file" accept="audio/*,.mp3,.wav,.m4a,.webm" className="sr-only" onChange={(event) => selectFile(event.target.files?.[0] || null)} />
            </button>
          </div>
        )}

        {error ? <p role="alert" className="mt-4 rounded-xl bg-[var(--status-danger-bg)] px-4 py-3 text-sm text-[var(--status-danger-fg)]">{error}</p> : null}
        {message ? <p role="status" className="mt-4 flex items-center gap-2 rounded-xl bg-[var(--status-success-bg)] px-4 py-3 text-sm text-[var(--status-success-fg)]"><Check size={16} />{message}</p> : null}

        <footer className="mt-6 flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose} disabled={busy || recording}>Vazgeç</Button>
          <Button type="button" onClick={submit} disabled={busy || recording}>
            {busy ? <LoaderCircle size={16} className="animate-spin" /> : <CloudUpload size={16} />}
            {busy ? 'Yükleniyor...' : 'Ses klonunu oluştur'}
          </Button>
        </footer>
      </section>
    </div>
  );
}

function VoiceRow({ voice, isPlaying, onPlay, mine = false, disabled = false }) {
  return (
    <div className="group grid grid-cols-[2.5rem_minmax(0,1fr)_auto] items-center gap-3 border-b border-[var(--border-subtle)] px-1 py-4 last:border-b-0 sm:grid-cols-[2.5rem_minmax(0,1fr)_auto_auto_auto]">
      <button type="button" onClick={onPlay} disabled={disabled} aria-label={`${voice.name} sesini ${isPlaying ? 'durdur' : 'dinle'}`} className="focus-ring inline-flex h-9 w-9 items-center justify-center rounded-full text-[var(--text-primary)] transition hover:bg-[var(--surface-container-high)] disabled:cursor-wait disabled:opacity-50">
        {isPlaying ? <Pause size={17} fill="currentColor" /> : <Play size={17} fill="currentColor" />}
      </button>
      <div className="min-w-0">
        <p className="truncate text-sm font-bold text-[var(--text-primary)]">{voice.name}</p>
        <p className="mt-1 truncate text-xs text-[var(--outline)]">{voice.description}</p>
      </div>
      <span className={`hidden rounded-md px-3 py-1.5 text-xs font-semibold sm:inline-flex ${mine ? 'bg-[var(--hover-accent-soft)] text-[var(--accent-primary)]' : 'bg-[var(--status-success-bg)] text-[var(--status-success-fg)]'}`}>{mine ? 'Sesim' : 'Herkese açık'}</span>
      <span className="hidden text-lg sm:inline" title={voice.locale}>{voice.flag}</span>
      <button type="button" className="focus-ring inline-flex h-9 w-9 items-center justify-center rounded-full text-[var(--outline)] transition hover:bg-[var(--surface-container-high)] hover:text-[var(--text-primary)]" aria-label={`${voice.name} seçenekleri`}><MoreHorizontal size={18} /></button>
    </div>
  );
}

export default function VoiceManager({ user, profilePayload, onProfileRefresh }) {
  const [cloneOpen, setCloneOpen] = useState(false);
  const [playingVoice, setPlayingVoice] = useState('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const previewAudioRef = useRef(null);
  const previewObjectUrlRef = useRef('');
  const voiceUploaded = Boolean(profilePayload?.avatar_setup_status?.checklist?.voice_uploaded || profilePayload?.voice_profile?.voice_id || profilePayload?.profile?.voice_id);

  const myVoice = {
    name: 'Klonlanmış sesim',
    description: 'Kişisel ses örneğiniz · VISUS ses klonlama',
    locale: 'Türkçe',
    flag: '🇹🇷',
  };

  const stopPreview = () => {
    const audio = previewAudioRef.current;
    if (audio) {
      audio.pause();
      audio.currentTime = 0;
    }
    setPlayingVoice('');
  };

  const discardPreview = () => {
    stopPreview();
    previewAudioRef.current = null;
    if (previewObjectUrlRef.current) {
      URL.revokeObjectURL(previewObjectUrlRef.current);
      previewObjectUrlRef.current = '';
    }
  };

  useEffect(() => () => {
    previewAudioRef.current?.pause?.();
    if (previewObjectUrlRef.current) URL.revokeObjectURL(previewObjectUrlRef.current);
  }, []);

  const togglePreview = async () => {
    if (playingVoice === myVoice.name) {
      stopPreview();
      return;
    }

    setPreviewLoading(true);
    setPreviewError('');
    try {
      if (!previewAudioRef.current) {
        const sample = await fetchVoiceSample(user?.id);
        previewObjectUrlRef.current = URL.createObjectURL(sample);
        const audio = new Audio(previewObjectUrlRef.current);
        audio.onended = () => setPlayingVoice('');
        audio.onerror = () => {
          setPlayingVoice('');
          setPreviewError('Ses örneği oynatılamadı. Lütfen yeniden deneyin.');
        };
        previewAudioRef.current = audio;
      }
      await previewAudioRef.current.play();
      setPlayingVoice(myVoice.name);
    } catch (error) {
      setPlayingVoice('');
      setPreviewError(error?.message || 'Ses örneği oynatılamadı. Lütfen yeniden deneyin.');
    } finally {
      setPreviewLoading(false);
    }
  };

  return (
    <section className="min-w-0 pb-16" aria-label="Ses yönetimi">
      <div className="pt-5 sm:pt-7">
        <button type="button" onClick={() => setCloneOpen(true)} className="focus-ring flex h-16 w-full max-w-sm items-center gap-4 rounded-full bg-[var(--surface-container-low)] px-5 text-left transition hover:bg-[var(--surface-container-high)]">
          <span className="inline-flex h-10 w-10 items-center justify-center rounded-full bg-[var(--surface-container-high)] text-[var(--text-primary)] shadow-sm"><Mic size={18} /></span>
          <span className="text-sm font-bold text-[var(--text-primary)]">Sesinizi klonlayın</span>
        </button>
      </div>

      <div className="mt-7 border-b border-[var(--border-subtle)] pb-3">
        <h2 className="px-1 text-sm font-bold text-[var(--text-primary)]">Seslerim</h2>
      </div>

      <div className="mt-5">
        {voiceUploaded ? (
          <>
            <VoiceRow voice={myVoice} mine disabled={previewLoading} isPlaying={playingVoice === myVoice.name} onPlay={togglePreview} />
            {previewLoading ? <p role="status" className="mt-3 text-sm text-[var(--text-secondary)]">Ses örneği hazırlanıyor...</p> : null}
            {previewError ? <p role="alert" className="mt-3 rounded-xl bg-[var(--status-danger-bg)] px-4 py-3 text-sm text-[var(--status-danger-fg)]">{previewError}</p> : null}
          </>
        ) : (
          <div className="flex min-h-64 flex-col items-center justify-center rounded-3xl border border-dashed border-[var(--outline-variant)] bg-[var(--surface-container-low)] px-5 text-center">
            <span className="inline-flex h-14 w-14 items-center justify-center rounded-full bg-[var(--surface-container-high)] text-[var(--accent-primary)]"><Mic size={23} /></span>
            <h2 className="mt-4 text-lg font-bold text-[var(--text-primary)]">Henüz klonlanmış sesiniz yok</h2>
            <p className="mt-2 max-w-md text-sm leading-6 text-[var(--text-secondary)]">Kaydınızı oluşturun veya temiz bir ses dosyası yükleyin; sesiniz burada görünecek.</p>
            <Button className="mt-5" onClick={() => setCloneOpen(true)}><Mic size={16} />Ses klonla</Button>
          </div>
        )}
      </div>

      <VoiceCloneModal
        open={cloneOpen}
        userId={user?.id}
        onClose={() => setCloneOpen(false)}
        onUploaded={async () => {
          discardPreview();
          await onProfileRefresh?.();
        }}
      />
    </section>
  );
}
