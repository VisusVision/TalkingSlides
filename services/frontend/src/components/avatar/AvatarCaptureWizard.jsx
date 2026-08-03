import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Camera,
  Check,
  Circle,
  Copy,
  Eye,
  FileVideo,
  Lightbulb,
  Mic,
  MonitorUp,
  RotateCcw,
  ShieldAlert,
  Sparkles,
  Square,
  Video,
  Volume2,
  X,
} from 'lucide-react';

const MAX_RECORDING_SECONDS = 30;

const COPY = {
  en: {
    eyebrow: 'Digital Twin',
    title: 'Create your realistic avatar',
    subtitle: 'Record one natural take. We will turn your appearance, voice, and movement into a reusable avatar.',
    steps: ['Setup', 'Record', 'Review'],
    webcam: 'Record with webcam',
    upload: 'Import media',
    enable: 'Check camera and microphone',
    openCameraWindow: 'Open camera recording window',
    cameraWindowHint: 'The recording opens on a separate local camera address and returns here automatically when finished.',
    popupBlocked: 'The camera window was blocked. Allow pop-ups for this site and try again.',
    cameraTransferFailed: 'The camera recording could not be transferred. Please try again.',
    enabling: 'Waiting for browser permission...',
    permissionHint: 'You will be asked to allow both devices. Nothing is recorded until you press Start.',
    start: 'Start 30-second performance recording',
    stop: 'Finish recording',
    retake: 'Record again',
    uploadTitle: 'Upload footage from your computer',
    uploadBody: 'Choose a clear portrait or one continuous video with audible speech.',
    chooseFile: 'Choose a file',
    selected: 'Selected',
    tipsTitle: 'Before you record',
    lightTip: 'Use soft, even light on your face.',
    eyeTip: 'Look directly into the camera.',
    soundTip: 'Choose a quiet room and speak naturally.',
    movementTip: 'While speaking, add a small nod, a brief smile, a natural blink, and a gentle eyebrow movement.',
    frameGuide: 'Keep your face inside the guide',
    cameraReady: 'Camera ready',
    microphoneReady: 'Microphone ready',
    defaultCamera: 'Camera',
    defaultMicrophone: 'Microphone',
    recording: 'Recording',
    reviewReady: 'Your take is ready to review',
    scenario: 'Speak naturally and keep looking at the lens.',
    language: 'English',
    back: 'Back',
    continue: 'Continue',
    unsupported: 'Camera recording is not supported by this browser. Upload a file instead.',
    permissionDenied: 'Camera or microphone permission is blocked',
    permissionHelpTitle: 'Having trouble with permissions?',
    permissionBody: '1. Open the camera or site-controls icon beside the address bar. 2. Set Camera and Microphone to Allow. 3. Return here and try again.',
    cameraPermission: 'Camera permission',
    microphonePermission: 'Microphone permission',
    blocked: 'Blocked',
    ready: 'Ready',
    allow: 'Allow access',
    unknown: 'Check required',
    retryPermissions: 'Try permissions again',
    useUpload: 'Continue with file upload',
    embeddedBrowserHelp: 'If the permission prompt does not appear in the Codex in-app browser, copy this address and open it in regular Chrome.',
    copyAddress: 'Copy address for Chrome',
    copied: 'Address copied',
    deviceError: 'The camera or microphone could not be started. Close other apps using them, then try again.',
    recordingError: 'The recording could not be completed. Please try again.',
    close: 'Close avatar creator',
    preview: 'Avatar footage preview',
    readAlongTitle: 'Read this while recording',
    readAlongText: 'Hello, I am creating my digital avatar. I speak naturally while looking into the camera. I give a small nod, briefly smile, blink normally, and lift my eyebrows gently. Then I return to a calm, neutral expression and continue explaining my lesson clearly.',
    nameTitle: 'Name your avatar',
    nameBody: 'Choose a short name that will help you recognize this avatar later.',
    nameLabel: 'Avatar name',
    namePlaceholder: 'For example: My lesson avatar',
    nextVoice: 'Choose voice',
    voiceTitle: 'Which voice should this avatar use?',
    voiceBody: 'You can keep your saved cloned voice or create the voice from this video recording.',
    savedVoice: 'Use a voice from My voices',
    savedVoiceBody: 'Uses your current cloned voice profile.',
    videoVoice: 'Use the voice in this video',
    videoVoiceBody: 'Extracts your speech from the recording and creates the avatar voice.',
    noSavedVoice: 'You do not have a saved voice yet.',
    consent: 'I confirm that I have permission to create and use this avatar, recording, and voice.',
    createAvatar: 'Create avatar profile',
    creatingAvatar: 'Creating avatar...',
    nameRequired: 'Enter a name for your avatar.',
    voiceRequired: 'Choose an available voice source.',
  },
  tr: {
    eyebrow: 'Dijital İkiz',
    title: 'Gerçekçi avatarını oluştur',
    subtitle: 'Tek ve doğal bir kayıt yap. Görünümünü, sesini ve hareketlerini tekrar kullanabileceğin bir avatara dönüştürelim.',
    steps: ['Hazırlık', 'Kayıt', 'Önizleme'],
    webcam: 'Webcam ile kaydet',
    upload: 'Görüntü içe aktar',
    enable: 'Kamera ve mikrofonu kontrol et',
    openCameraWindow: 'Kamera kayıt penceresini aç',
    cameraWindowHint: 'Kayıt ayrı yerel kamera adresinde açılır ve tamamlanınca otomatik olarak bu ekrana geri gelir.',
    popupBlocked: 'Kamera penceresi engellendi. Bu site için açılır pencerelere izin verip tekrar dene.',
    cameraTransferFailed: 'Kamera kaydı ana ekrana aktarılamadı. Lütfen tekrar dene.',
    enabling: 'Tarayıcı izni bekleniyor...',
    permissionHint: 'İki aygıt için de izin istenir. “Kaydı başlat” demeden hiçbir şey kaydedilmez.',
    start: '30 saniyelik performans kaydını başlat',
    stop: 'Kaydı tamamla',
    retake: 'Yeniden kaydet',
    uploadTitle: 'Bilgisayarından görüntü yükle',
    uploadBody: 'Net bir portre veya sesinin duyulduğu kesintisiz bir video seç.',
    chooseFile: 'Dosya seç',
    selected: 'Seçilen',
    tipsTitle: 'Kayda başlamadan önce',
    lightTip: 'Yüzün yumuşak ve eşit ışık alsın.',
    eyeTip: 'Doğrudan kamera lensine bak.',
    soundTip: 'Sessiz bir ortamda doğal biçimde konuş.',
    movementTip: 'Konuşurken hafifçe başını salla, kısa bir gülümseme yap, doğal göz kırp ve kaşlarını nazikçe hareket ettir.',
    frameGuide: 'Yüzünü kılavuzun içinde tut',
    cameraReady: 'Kamera hazır',
    microphoneReady: 'Mikrofon hazır',
    defaultCamera: 'Kamera',
    defaultMicrophone: 'Mikrofon',
    recording: 'Kayıt yapılıyor',
    reviewReady: 'Kaydın önizlemeye hazır',
    scenario: 'Doğal konuş ve gözlerini lensten ayırma.',
    language: 'Türkçe',
    back: 'Geri',
    continue: 'Devam et',
    unsupported: 'Bu tarayıcı kamera kaydını desteklemiyor. Bunun yerine bilgisayarından dosya yükleyebilirsin.',
    permissionDenied: 'Kamera veya mikrofon izni engellenmiş',
    permissionHelpTitle: 'İzin sorunu mu yaşıyorsun?',
    permissionBody: '1. Adres çubuğundaki kamera veya site ayarları simgesine tıkla. 2. Kamera ve Mikrofonu “İzin ver” yap. 3. Bu ekrana dönüp tekrar dene.',
    cameraPermission: 'Kamera izni',
    microphonePermission: 'Mikrofon izni',
    blocked: 'Engellendi',
    ready: 'Hazır',
    allow: 'İzin ver',
    unknown: 'Kontrol gerekli',
    retryPermissions: 'İzinleri tekrar dene',
    useUpload: 'Dosya yükleyerek devam et',
    embeddedBrowserHelp: 'Codex uygulama içi tarayıcısında izin penceresi görünmüyorsa bu adresi kopyalayıp normal Chrome’da aç.',
    copyAddress: 'Chrome için adresi kopyala',
    copied: 'Adres kopyalandı',
    deviceError: 'Kamera veya mikrofon başlatılamadı. Bunları kullanan diğer uygulamaları kapatıp tekrar dene.',
    recordingError: 'Kayıt tamamlanamadı. Lütfen tekrar dene.',
    close: 'Avatar oluşturucuyu kapat',
    preview: 'Avatar görüntüsü önizlemesi',
    readAlongTitle: 'Kayıt sırasında bu metni oku',
    readAlongText: 'Merhaba, dijital avatarımı oluşturuyorum. Kameraya bakarak doğal biçimde konuşuyorum. Hafifçe başımı sallıyor, kısa bir an gülümsüyor, normal şekilde göz kırpıyor ve kaşlarımı nazikçe kaldırıyorum. Sonra sakin ve nötr ifademe dönüp dersimi açık bir şekilde anlatmaya devam ediyorum.',
    nameTitle: 'Avatarına bir ad ver',
    nameBody: 'Daha sonra kolayca tanıyabileceğin kısa bir avatar adı belirle.',
    nameLabel: 'Avatar adı',
    namePlaceholder: 'Örneğin: Ders avatarım',
    nextVoice: 'Ses seçimine geç',
    voiceTitle: 'Bu avatar hangi sesi kullansın?',
    voiceBody: 'Kayıtlı klon sesini kullanabilir veya bu videodaki konuşmadan yeni avatar sesini oluşturabilirsin.',
    savedVoice: 'Seslerimden birini kullan',
    savedVoiceBody: 'Mevcut klonlanmış ses profilini kullanır.',
    videoVoice: 'Videodaki sesi kullan',
    videoVoiceBody: 'Kayıttaki konuşmanı ayırır ve avatar sesi olarak hazırlar.',
    noSavedVoice: 'Henüz kayıtlı bir sesin bulunmuyor.',
    consent: 'Bu avatarı, kaydı ve sesi oluşturma ve kullanma iznine sahip olduğumu onaylıyorum.',
    createAvatar: 'Avatar profilini oluştur',
    creatingAvatar: 'Avatar oluşturuluyor...',
    nameRequired: 'Avatarın için bir ad yaz.',
    voiceRequired: 'Kullanılabilir bir ses kaynağı seç.',
  },
};

function preferredRecordingOptions() {
  const candidates = [
    'video/webm;codecs=vp8,opus',
    'video/webm',
    'video/webm;codecs=vp9,opus',
  ];
  const mimeType = candidates.find((candidate) => window.MediaRecorder?.isTypeSupported?.(candidate));
  return mimeType ? { mimeType } : undefined;
}

function stopStream(stream) {
  stream?.getTracks?.().forEach((track) => track.stop?.());
}

function fileFromRecording(chunks, mimeType) {
  const type = mimeType || chunks[0]?.type || 'video/webm';
  const blob = new Blob(chunks, { type });
  return new File([blob], `avatar-webcam-${Date.now()}.webm`, { type });
}

async function readMediaPermissions() {
  const result = { camera: 'unknown', microphone: 'unknown' };
  if (!navigator.permissions?.query) return result;
  await Promise.all(['camera', 'microphone'].map(async (name) => {
    try {
      const status = await navigator.permissions.query({ name });
      result[name] = status.state;
    } catch {
      result[name] = 'unknown';
    }
  }));
  return result;
}

function RecordingTip({ icon: Icon, children }) {
  return (
    <li className="flex gap-3 text-sm leading-5 text-slate-600">
      <span className="mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-violet-50 text-violet-600"><Icon size={16} /></span>
      <span>{children}</span>
    </li>
  );
}

function PermissionRow({ icon: Icon, label, state, copy }) {
  const blocked = state === 'denied';
  const ready = state === 'granted';
  const stateLabel = ready ? copy.ready : blocked ? copy.blocked : state === 'prompt' ? copy.allow : copy.unknown;
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2.5">
      <span className="flex items-center gap-2 text-sm font-semibold text-slate-700"><Icon size={16} />{label}</span>
      <span className={`rounded-full px-2.5 py-1 text-xs font-bold ${ready ? 'bg-emerald-50 text-emerald-700' : blocked ? 'bg-red-50 text-red-600' : 'bg-amber-50 text-amber-700'}`}>
        {stateLabel}
      </span>
    </div>
  );
}

export default function AvatarCaptureWizard({
  locale = 'en',
  hasSavedVoice = false,
  externalCaptureOrigin = '',
  onBack,
  onComplete,
}) {
  const copy = locale === 'tr' ? COPY.tr : COPY.en;
  const [mode, setMode] = useState('webcam');
  const [stream, setStream] = useState(null);
  const [phase, setPhase] = useState('idle');
  const [seconds, setSeconds] = useState(0);
  const [selectedFile, setSelectedFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState('');
  const [error, setError] = useState('');
  const [permissionIssue, setPermissionIssue] = useState(false);
  const [permissions, setPermissions] = useState({ camera: 'unknown', microphone: 'unknown' });
  const [deviceLabels, setDeviceLabels] = useState({ camera: '', microphone: '' });
  const [addressCopied, setAddressCopied] = useState(false);
  const [detailsStep, setDetailsStep] = useState('');
  const [avatarName, setAvatarName] = useState('');
  const [voiceSource, setVoiceSource] = useState(hasSavedVoice ? 'existing' : 'video');
  const [consentConfirmed, setConsentConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const liveVideoRef = useRef(null);
  const fileInputRef = useRef(null);
  const recorderRef = useRef(null);
  const chunksRef = useRef([]);
  const deviceRequestIdRef = useRef(0);
  const externalCameraWindowRef = useRef(null);

  const recordingSupported = Boolean(
    typeof navigator !== 'undefined'
      && navigator.mediaDevices?.getUserMedia
      && typeof window.MediaRecorder === 'function',
  );

  const activeStep = selectedFile ? 3 : ['camera', 'recording'].includes(phase) ? 2 : 1;
  const remaining = useMemo(() => MAX_RECORDING_SECONDS - seconds, [seconds]);
  const teleprompterOffset = useMemo(
    () => Math.round((seconds / MAX_RECORDING_SECONDS) * 96),
    [seconds],
  );

  useEffect(() => {
    let active = true;
    void readMediaPermissions().then((states) => {
      if (!active) return;
      setPermissions(states);
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (liveVideoRef.current && stream) {
      liveVideoRef.current.srcObject = stream;
      const playResult = liveVideoRef.current.play?.();
      playResult?.catch?.(() => {});
    }
  }, [stream, phase]);

  useEffect(() => () => {
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
    stopStream(stream);
  }, [stream]);

  useEffect(() => () => {
    deviceRequestIdRef.current += 1;
    externalCameraWindowRef.current?.close?.();
  }, []);

  useEffect(() => () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
  }, [previewUrl]);

  useEffect(() => {
    if (phase !== 'recording') return undefined;
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      const elapsed = Math.min(MAX_RECORDING_SECONDS, Math.floor((Date.now() - startedAt) / 1000));
      setSeconds(elapsed);
      if (elapsed >= MAX_RECORDING_SECONDS && recorderRef.current?.state === 'recording') recorderRef.current.stop();
    }, 250);
    return () => window.clearInterval(timer);
  }, [phase]);

  const selectFile = (file) => {
    if (!file) return;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setSelectedFile(file);
    setPreviewUrl(URL.createObjectURL(file));
    setPhase('ready');
    setVoiceSource(hasSavedVoice ? 'existing' : (file.type.startsWith('video/') ? 'video' : ''));
    setError('');
  };

  useEffect(() => {
    if (!externalCaptureOrigin) return undefined;

    const handleExternalCameraMessage = (event) => {
      if (event.origin !== externalCaptureOrigin || event.source !== externalCameraWindowRef.current) return;

      if (event.data?.type === 'visus-avatar-camera-error') {
        setError(event.data.message || copy.cameraTransferFailed);
        return;
      }

      if (event.data?.type !== 'visus-avatar-camera-recording' || !(event.data.blob instanceof Blob)) return;

      const mimeType = event.data.mimeType || event.data.blob.type || 'video/webm';
      const extension = mimeType.includes('mp4') ? 'mp4' : 'webm';
      selectFile(new File(
        [event.data.blob],
        `avatar-webcam-${Date.now()}.${extension}`,
        { type: mimeType },
      ));
      setPermissionIssue(false);
      externalCameraWindowRef.current = null;
    };

    window.addEventListener('message', handleExternalCameraMessage);
    return () => window.removeEventListener('message', handleExternalCameraMessage);
  }, [copy.cameraTransferFailed, externalCaptureOrigin, previewUrl]);

  const openExternalCamera = () => {
    if (!externalCaptureOrigin) {
      void enableDevices();
      return;
    }

    const params = new URLSearchParams({
      locale,
      parentOrigin: window.location.origin,
    });
    const popup = window.open(
      `${externalCaptureOrigin}/avatar-camera-capture.html?${params.toString()}`,
      'visus-avatar-camera-capture',
      'popup=yes,width=1100,height=820',
    );
    if (!popup) {
      setError(copy.popupBlocked);
      return;
    }
    externalCameraWindowRef.current = popup;
    popup.focus?.();
    setPermissionIssue(false);
    setError('');
  };

  const switchMode = (nextMode) => {
    if (nextMode === mode) return;
    deviceRequestIdRef.current += 1;
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
    stopStream(stream);
    externalCameraWindowRef.current?.close?.();
    externalCameraWindowRef.current = null;
    setStream(null);
    setMode(nextMode);
    setPhase(selectedFile ? 'ready' : 'idle');
    setError('');
  };

  const enableDevices = async () => {
    setError('');
    setPermissionIssue(false);
    if (!recordingSupported) {
      setError(copy.unsupported);
      return;
    }
    const requestId = deviceRequestIdRef.current + 1;
    deviceRequestIdRef.current = requestId;
    setPhase('requesting');
    try {
      const nextStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      if (requestId !== deviceRequestIdRef.current) {
        stopStream(nextStream);
        return;
      }
      stopStream(stream);
      const videoTrack = nextStream.getVideoTracks?.()[0];
      const audioTrack = nextStream.getAudioTracks?.()[0];
      setDeviceLabels({
        camera: videoTrack?.label || copy.defaultCamera,
        microphone: audioTrack?.label || copy.defaultMicrophone,
      });
      setPermissions({ camera: 'granted', microphone: 'granted' });
      setStream(nextStream);
      setPhase('camera');
    } catch (deviceError) {
      if (requestId !== deviceRequestIdRef.current) return;
      console.warn(
        '[AvatarCaptureWizard] getUserMedia failed',
        deviceError?.name || 'UnknownError',
        deviceError?.message || '',
        deviceError?.constraint || '',
      );
      const states = await readMediaPermissions();
      const permissionDenied = ['NotAllowedError', 'PermissionDeniedError', 'SecurityError'].includes(deviceError?.name);
      if (permissionDenied) {
        setPermissions({
          camera: states.camera === 'unknown' ? 'denied' : states.camera,
          microphone: states.microphone === 'unknown' ? 'denied' : states.microphone,
        });
        setPermissionIssue(true);
      } else {
        setError(copy.deviceError);
      }
      setPhase('idle');
    }
  };

  const startRecording = () => {
    if (!stream) return;
    setError('');
    setSeconds(0);
    chunksRef.current = [];
    try {
      const recorder = new window.MediaRecorder(stream, preferredRecordingOptions());
      recorderRef.current = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data?.size) chunksRef.current.push(event.data);
      };
      recorder.onerror = () => {
        setError(copy.recordingError);
        setPhase('camera');
      };
      recorder.onstop = () => {
        try {
          selectFile(fileFromRecording(chunksRef.current, recorder.mimeType));
          stopStream(stream);
          setStream(null);
        } catch {
          setError(copy.recordingError);
          setPhase('camera');
        }
      };
      recorder.start(250);
      setPhase('recording');
    } catch {
      setError(copy.recordingError);
    }
  };

  const stopRecording = () => {
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
  };

  const resetWebcam = () => {
    setSelectedFile(null);
    setPhase('idle');
    setSeconds(0);
    setError('');
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl('');
    setDetailsStep('');
  };

  const continueToDetails = () => {
    if (!selectedFile) return;
    setError('');
    setDetailsStep('name');
  };

  const continueToVoice = () => {
    if (!avatarName.trim()) {
      setError(copy.nameRequired);
      return;
    }
    setError('');
    setDetailsStep('voice');
  };

  const createAvatar = async () => {
    const videoSelected = Boolean(selectedFile?.type?.startsWith('video/'));
    if (!avatarName.trim()) {
      setDetailsStep('name');
      setError(copy.nameRequired);
      return;
    }
    if (!voiceSource || (voiceSource === 'existing' && !hasSavedVoice) || (voiceSource === 'video' && !videoSelected)) {
      setError(copy.voiceRequired);
      return;
    }
    if (!consentConfirmed) return;
    setSubmitting(true);
    setError('');
    try {
      await onComplete?.({
        file: selectedFile,
        avatarName: avatarName.trim(),
        voiceSource,
        consentConfirmed: true,
      });
    } catch (submitError) {
      setError(submitError?.message || copy.recordingError);
    } finally {
      setSubmitting(false);
    }
  };

  const handleBack = () => {
    deviceRequestIdRef.current += 1;
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
    stopStream(stream);
    externalCameraWindowRef.current?.close?.();
    externalCameraWindowRef.current = null;
    onBack?.();
  };

  const copyChromeAddress = async () => {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}/avatar`);
      setAddressCopied(true);
      window.setTimeout(() => setAddressCopied(false), 2500);
    } catch {
      setError(`${window.location.origin}/avatar`);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] overflow-y-auto bg-[#f5f5f7] text-slate-950" data-testid="avatar-capture-wizard">
      <div className="mx-auto min-h-full w-full max-w-[1180px] px-4 py-5 sm:px-7 sm:py-7">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5 text-sm font-extrabold tracking-tight">
            <span className="inline-flex h-8 w-8 items-center justify-center rounded-xl bg-violet-600 text-white"><Sparkles size={17} /></span>
            {copy.eyebrow}
          </div>
          <button type="button" aria-label={copy.close} onClick={handleBack} className="focus-ring inline-flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 shadow-sm transition hover:text-slate-950">
            <X size={19} />
          </button>
        </div>

        <header className="mx-auto mt-4 max-w-3xl text-center">
          <h2 className="font-['Manrope'] text-2xl font-extrabold tracking-[-0.04em] sm:text-4xl">{copy.title}</h2>
          <p className="mx-auto mt-2 max-w-2xl text-sm leading-6 text-slate-500">{copy.subtitle}</p>
        </header>

        <ol className="mx-auto mt-5 flex max-w-xl items-center" aria-label={copy.title}>
          {copy.steps.map((step, index) => {
            const number = index + 1;
            const complete = activeStep > number;
            const active = activeStep === number;
            return (
              <li key={step} className="flex flex-1 items-center last:flex-none">
                <span className="flex items-center gap-2">
                  <span className={`inline-flex h-7 w-7 items-center justify-center rounded-full text-xs font-extrabold ${complete || active ? 'bg-violet-600 text-white' : 'border border-slate-300 bg-white text-slate-400'}`}>
                    {complete ? <Check size={14} /> : number}
                  </span>
                  <span className={`hidden text-xs font-bold sm:inline ${active ? 'text-slate-900' : 'text-slate-400'}`}>{step}</span>
                </span>
                {number < copy.steps.length ? <span className={`mx-3 h-px flex-1 ${complete ? 'bg-violet-500' : 'bg-slate-200'}`} /> : null}
              </li>
            );
          })}
        </ol>

        <div className="mt-5 overflow-hidden rounded-[1.75rem] border border-slate-200 bg-white shadow-[0_24px_80px_rgba(15,23,42,0.09)]">
          <div className="border-b border-slate-200 p-2">
            <div className="mx-auto grid max-w-lg grid-cols-2 rounded-xl bg-slate-100 p-1" role="tablist" aria-label={copy.title}>
              <button type="button" role="tab" aria-selected={mode === 'webcam'} onClick={() => switchMode('webcam')} className={`focus-ring rounded-lg px-4 py-2.5 text-sm font-bold transition ${mode === 'webcam' ? 'bg-white text-slate-950 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}>
                {copy.webcam}
              </button>
              <button type="button" role="tab" aria-selected={mode === 'upload'} onClick={() => switchMode('upload')} className={`focus-ring rounded-lg px-4 py-2.5 text-sm font-bold transition ${mode === 'upload' ? 'bg-white text-slate-950 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}>
                {copy.upload}
              </button>
            </div>
          </div>

          <div className="grid lg:grid-cols-[minmax(0,1.7fr)_minmax(280px,0.7fr)]">
            <div className="p-4 sm:p-6">
              <div className="relative flex min-h-[340px] items-center justify-center overflow-hidden rounded-2xl bg-[#101114] sm:min-h-[430px]">
                {mode === 'webcam' ? (
                  <>
                    {(phase === 'camera' || phase === 'recording') && stream ? (
                      <>
                        <video ref={liveVideoRef} muted playsInline aria-label={copy.preview} className="absolute inset-0 h-full w-full scale-x-[-1] object-cover" />
                        <div className="pointer-events-none absolute left-1/2 top-1/2 h-[70%] w-[48%] -translate-x-1/2 -translate-y-1/2 rounded-[45%] border-2 border-dashed border-white/65 shadow-[0_0_0_999px_rgba(0,0,0,0.12)]" />
                        <span className="absolute left-1/2 top-4 -translate-x-1/2 rounded-full bg-black/60 px-3 py-1.5 text-xs font-bold text-white backdrop-blur">{copy.frameGuide}</span>
                        <div
                          data-testid="avatar-teleprompter"
                          data-offset={phase === 'recording' ? teleprompterOffset : 0}
                          className="pointer-events-none absolute left-1/2 top-14 z-10 w-[88%] max-w-2xl rounded-2xl border border-white/20 bg-black/65 px-5 py-3 text-center text-white shadow-xl backdrop-blur-md transition-transform duration-300"
                          style={{ transform: `translate(-50%, ${phase === 'recording' ? teleprompterOffset : 0}px)` }}
                        >
                          <p className="text-[10px] font-extrabold uppercase tracking-[0.16em] text-violet-200">{copy.readAlongTitle}</p>
                          <p className="mt-1 text-sm font-semibold leading-5 sm:text-base sm:leading-6">{copy.readAlongText}</p>
                        </div>
                      </>
                    ) : phase === 'ready' && previewUrl ? (
                      <video src={previewUrl} controls playsInline aria-label={copy.preview} className="absolute inset-0 h-full w-full object-contain" />
                    ) : permissionIssue ? (
                      <div role="alert" className="w-full max-w-lg px-5 py-6 text-center text-white">
                        <span className="mx-auto inline-flex h-14 w-14 items-center justify-center rounded-2xl bg-red-500/15 text-red-300"><ShieldAlert size={26} /></span>
                        <h3 className="mt-4 text-lg font-extrabold">{copy.permissionDenied}</h3>
                        <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-white/60">{copy.permissionBody}</p>
                        <div className="mx-auto mt-4 grid max-w-md gap-2 sm:grid-cols-2">
                          <PermissionRow icon={Camera} label={copy.cameraPermission} state={permissions.camera} copy={copy} />
                          <PermissionRow icon={Mic} label={copy.microphonePermission} state={permissions.microphone} copy={copy} />
                        </div>
                        <div className="mt-5 flex flex-wrap justify-center gap-2">
                          <button type="button" onClick={externalCaptureOrigin ? openExternalCamera : enableDevices} className="focus-ring rounded-full bg-violet-600 px-5 py-2.5 text-sm font-extrabold text-white transition hover:bg-violet-500">{externalCaptureOrigin ? copy.openCameraWindow : copy.retryPermissions}</button>
                          <button type="button" onClick={() => switchMode('upload')} className="focus-ring rounded-full border border-white/15 bg-white/10 px-5 py-2.5 text-sm font-bold text-white transition hover:bg-white/15">{copy.useUpload}</button>
                        </div>
                        <p className="mx-auto mt-4 max-w-md text-xs leading-5 text-white/50">{copy.embeddedBrowserHelp}</p>
                        <button type="button" onClick={copyChromeAddress} className="focus-ring mx-auto mt-2 inline-flex items-center gap-2 rounded-full px-3 py-2 text-xs font-bold text-violet-200 transition hover:bg-white/10"><Copy size={14} />{addressCopied ? copy.copied : copy.copyAddress}</button>
                      </div>
                    ) : (
                      <div className="flex max-w-md flex-col items-center px-6 text-center text-white">
                        <span className="mb-5 inline-flex h-16 w-16 items-center justify-center rounded-2xl bg-white/[0.07] text-violet-300"><Video size={29} /></span>
                        <button type="button" disabled={phase === 'requesting'} onClick={externalCaptureOrigin ? openExternalCamera : enableDevices} className="focus-ring inline-flex items-center gap-2 rounded-full bg-violet-600 px-5 py-3 text-sm font-extrabold text-white transition hover:bg-violet-500 active:scale-95 disabled:cursor-wait disabled:opacity-70">
                          <Camera size={17} />
                          <span>{phase === 'requesting' ? copy.enabling : (externalCaptureOrigin ? copy.openCameraWindow : copy.enable)}</span>
                        </button>
                        <p className="mt-3 max-w-sm text-xs leading-5 text-white/45">{externalCaptureOrigin ? copy.cameraWindowHint : copy.permissionHint}</p>
                      </div>
                    )}

                    {phase === 'camera' ? (
                      <button type="button" onClick={startRecording} className="focus-ring absolute bottom-5 inline-flex items-center gap-2 rounded-full bg-white px-5 py-3 text-sm font-extrabold text-slate-950 shadow-xl">
                        <Circle size={16} className="fill-red-500 text-red-500" />{copy.start}
                      </button>
                    ) : null}
                    {phase === 'recording' ? (
                      <div className="absolute inset-x-0 bottom-5 flex items-center justify-center gap-3">
                        <span className="inline-flex items-center gap-2 rounded-full bg-red-500 px-3 py-2 text-sm font-extrabold text-white"><span className="h-2 w-2 animate-pulse rounded-full bg-white" />{copy.recording} · 00:{String(remaining).padStart(2, '0')}</span>
                        <button type="button" onClick={stopRecording} className="focus-ring inline-flex items-center gap-2 rounded-full bg-white px-5 py-2.5 text-sm font-extrabold text-slate-950 shadow-xl"><Square size={14} className="fill-red-500 text-red-500" />{copy.stop}</button>
                      </div>
                    ) : null}
                    {phase === 'ready' && selectedFile ? (
                      <button type="button" onClick={resetWebcam} className="focus-ring absolute right-4 top-4 inline-flex items-center gap-2 rounded-full bg-black/65 px-4 py-2 text-xs font-bold text-white backdrop-blur"><RotateCcw size={14} />{copy.retake}</button>
                    ) : null}
                  </>
                ) : (
                  <div className="flex w-full max-w-md flex-col items-center px-6 text-center text-white">
                    {selectedFile && previewUrl ? (
                      selectedFile.type.startsWith('image/') ? (
                        <img src={previewUrl} alt={copy.preview} className="absolute inset-0 h-full w-full object-contain" />
                      ) : (
                        <video src={previewUrl} controls playsInline aria-label={copy.preview} className="absolute inset-0 h-full w-full object-contain" />
                      )
                    ) : (
                      <>
                        <span className="inline-flex h-16 w-16 items-center justify-center rounded-2xl bg-white/[0.07] text-violet-300"><MonitorUp size={28} /></span>
                        <h3 className="mt-5 text-lg font-extrabold">{copy.uploadTitle}</h3>
                        <p className="mt-2 text-sm leading-6 text-white/50">{copy.uploadBody}</p>
                        <button type="button" onClick={() => fileInputRef.current?.click()} className="focus-ring mt-5 inline-flex items-center gap-2 rounded-full bg-violet-600 px-5 py-3 text-sm font-extrabold text-white transition hover:bg-violet-500 active:scale-95"><FileVideo size={17} />{copy.chooseFile}</button>
                      </>
                    )}
                    <input ref={fileInputRef} type="file" accept="image/*,video/*" className="sr-only" tabIndex={-1} aria-label={copy.chooseFile} onChange={(event) => selectFile(event.target.files?.[0] || null)} />
                    {selectedFile ? (
                      <button type="button" onClick={() => fileInputRef.current?.click()} className="focus-ring absolute right-4 top-4 max-w-[75%] truncate rounded-full bg-black/65 px-4 py-2 text-xs font-bold text-white backdrop-blur" title={selectedFile.name}>{copy.selected}: {selectedFile.name}</button>
                    ) : null}
                  </div>
                )}
              </div>

              {error ? <p role="alert" className="mt-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm font-medium text-red-700">{error}</p> : null}

              {(phase === 'camera' || phase === 'recording') && stream ? (
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  <div className="flex min-w-0 items-center gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2.5">
                    <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-emerald-100 text-emerald-700"><Camera size={16} /></span>
                    <span className="min-w-0"><span className="block text-xs font-extrabold text-emerald-800">{copy.cameraReady}</span><span className="block truncate text-xs text-emerald-700/70">{deviceLabels.camera}</span></span>
                  </div>
                  <div className="flex min-w-0 items-center gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2.5">
                    <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-emerald-100 text-emerald-700"><Mic size={16} /></span>
                    <span className="min-w-0 flex-1"><span className="block text-xs font-extrabold text-emerald-800">{copy.microphoneReady}</span><span className="block truncate text-xs text-emerald-700/70">{deviceLabels.microphone}</span></span>
                    <span className="flex items-end gap-0.5" aria-hidden="true"><i className="h-2 w-1 rounded bg-emerald-500" /><i className="h-4 w-1 rounded bg-emerald-500" /><i className="h-3 w-1 rounded bg-emerald-500" /></span>
                  </div>
                </div>
              ) : null}
            </div>

            <aside className="border-t border-slate-200 bg-slate-50/80 p-5 lg:border-l lg:border-t-0 sm:p-6">
              <p className="text-xs font-extrabold uppercase tracking-[0.16em] text-violet-600">{activeStep === 3 ? copy.reviewReady : copy.tipsTitle}</p>
              <ul className="mt-5 space-y-4">
                <RecordingTip icon={Lightbulb}>{copy.lightTip}</RecordingTip>
                <RecordingTip icon={Eye}>{copy.eyeTip}</RecordingTip>
                <RecordingTip icon={Volume2}>{copy.soundTip}</RecordingTip>
                <RecordingTip icon={Video}>{copy.movementTip}</RecordingTip>
              </ul>
              <div className="mt-6 rounded-2xl border border-violet-100 bg-violet-50 p-4">
                <div className="flex items-center gap-2 text-sm font-extrabold text-violet-950"><Sparkles size={16} className="text-violet-600" />{copy.scenario}</div>
                <span className="mt-3 inline-flex rounded-full bg-white px-3 py-1 text-xs font-bold text-violet-700 shadow-sm">{copy.language}</span>
              </div>
              <div className="mt-3 rounded-2xl border border-slate-200 bg-white p-4">
                <p className="flex items-center gap-2 text-xs font-extrabold text-slate-800"><ShieldAlert size={15} className="text-slate-500" />{copy.permissionHelpTitle}</p>
                <p className="mt-2 text-xs leading-5 text-slate-500">{copy.permissionBody}</p>
                <p className="mt-2 text-xs leading-5 text-slate-500">{copy.embeddedBrowserHelp}</p>
                <button type="button" onClick={copyChromeAddress} className="focus-ring mt-3 inline-flex items-center gap-2 rounded-full bg-slate-100 px-3 py-2 text-xs font-extrabold text-slate-700 transition hover:bg-slate-200"><Copy size={14} />{addressCopied ? copy.copied : copy.copyAddress}</button>
              </div>
            </aside>
          </div>

          <footer className="flex items-center justify-between gap-4 border-t border-slate-200 bg-white px-4 py-4 sm:px-6">
            <button type="button" onClick={handleBack} className="focus-ring rounded-full px-4 py-2.5 text-sm font-extrabold text-slate-600 transition hover:bg-slate-100 hover:text-slate-950">{copy.back}</button>
            <button type="button" disabled={!selectedFile} onClick={continueToDetails} className="focus-ring min-w-40 rounded-full bg-violet-600 px-6 py-3 text-sm font-extrabold text-white transition hover:bg-violet-500 active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400">{copy.continue}</button>
          </footer>
        </div>

        {detailsStep ? (
          <div className="fixed inset-0 z-[120] flex items-center justify-center bg-slate-950/65 p-4 backdrop-blur-sm">
            <section role="dialog" aria-modal="true" aria-labelledby="avatar-details-title" className="w-full max-w-xl rounded-[1.75rem] bg-white p-6 shadow-2xl sm:p-8">
              {detailsStep === 'name' ? (
                <>
                  <p className="text-xs font-extrabold uppercase tracking-[0.16em] text-violet-600">1 / 2</p>
                  <h3 id="avatar-details-title" className="mt-3 font-['Manrope'] text-2xl font-extrabold">{copy.nameTitle}</h3>
                  <p className="mt-2 text-sm leading-6 text-slate-500">{copy.nameBody}</p>
                  <label className="mt-6 block text-sm font-bold text-slate-800">
                    {copy.nameLabel}
                    <input autoFocus type="text" maxLength={120} value={avatarName} onChange={(event) => setAvatarName(event.target.value)} placeholder={copy.namePlaceholder} className="focus-ring mt-2 h-12 w-full rounded-xl border border-slate-200 px-4 text-sm outline-none" />
                  </label>
                  {error ? <p role="alert" className="mt-4 rounded-xl bg-red-50 px-4 py-3 text-sm font-semibold text-red-700">{error}</p> : null}
                  <div className="mt-7 flex justify-between gap-3">
                    <button type="button" onClick={() => { setDetailsStep(''); setError(''); }} className="focus-ring rounded-full px-4 py-2.5 text-sm font-bold text-slate-600 hover:bg-slate-100">{copy.back}</button>
                    <button type="button" onClick={continueToVoice} className="focus-ring rounded-full bg-violet-600 px-6 py-3 text-sm font-extrabold text-white hover:bg-violet-500">{copy.nextVoice}</button>
                  </div>
                </>
              ) : (
                <>
                  <p className="text-xs font-extrabold uppercase tracking-[0.16em] text-violet-600">2 / 2</p>
                  <h3 id="avatar-details-title" className="mt-3 font-['Manrope'] text-2xl font-extrabold">{copy.voiceTitle}</h3>
                  <p className="mt-2 text-sm leading-6 text-slate-500">{copy.voiceBody}</p>
                  <div className="mt-6 space-y-3" role="radiogroup" aria-label={copy.voiceTitle}>
                    <label className={`flex gap-4 rounded-2xl border p-4 ${hasSavedVoice ? 'cursor-pointer border-slate-200' : 'cursor-not-allowed border-slate-100 opacity-55'}`}>
                      <input type="radio" name="avatar-voice-source" value="existing" checked={voiceSource === 'existing'} disabled={!hasSavedVoice || submitting} onChange={() => setVoiceSource('existing')} className="mt-1" />
                      <span><span className="block text-sm font-extrabold text-slate-900">{copy.savedVoice}</span><span className="mt-1 block text-xs leading-5 text-slate-500">{hasSavedVoice ? copy.savedVoiceBody : copy.noSavedVoice}</span></span>
                    </label>
                    <label className={`flex gap-4 rounded-2xl border p-4 ${selectedFile?.type?.startsWith('video/') ? 'cursor-pointer border-slate-200' : 'cursor-not-allowed border-slate-100 opacity-55'}`}>
                      <input type="radio" name="avatar-voice-source" value="video" checked={voiceSource === 'video'} disabled={!selectedFile?.type?.startsWith('video/') || submitting} onChange={() => setVoiceSource('video')} className="mt-1" />
                      <span><span className="block text-sm font-extrabold text-slate-900">{copy.videoVoice}</span><span className="mt-1 block text-xs leading-5 text-slate-500">{copy.videoVoiceBody}</span></span>
                    </label>
                  </div>
                  <label className="mt-5 flex items-start gap-3 rounded-2xl bg-slate-50 p-4 text-xs font-semibold leading-5 text-slate-600">
                    <input type="checkbox" checked={consentConfirmed} disabled={submitting} onChange={(event) => setConsentConfirmed(event.target.checked)} className="mt-0.5" />
                    <span>{copy.consent}</span>
                  </label>
                  {error ? <p role="alert" className="mt-4 rounded-xl bg-red-50 px-4 py-3 text-sm font-semibold text-red-700">{error}</p> : null}
                  <div className="mt-7 flex justify-between gap-3">
                    <button type="button" disabled={submitting} onClick={() => { setDetailsStep('name'); setError(''); }} className="focus-ring rounded-full px-4 py-2.5 text-sm font-bold text-slate-600 hover:bg-slate-100">{copy.back}</button>
                    <button type="button" disabled={!consentConfirmed || submitting} onClick={createAvatar} className="focus-ring inline-flex items-center gap-2 rounded-full bg-violet-600 px-6 py-3 text-sm font-extrabold text-white hover:bg-violet-500 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400">
                      {submitting ? <Sparkles size={16} className="animate-pulse" /> : <Check size={16} />}
                      {submitting ? copy.creatingAvatar : copy.createAvatar}
                    </button>
                  </div>
                </>
              )}
            </section>
          </div>
        ) : null}
      </div>
    </div>
  );
}
