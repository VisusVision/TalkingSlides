import { CheckCircle2, ChevronDown, NotebookPen } from 'lucide-react';
import Button from '../ui/Button';
import SurfaceCard from '../ui/SurfaceCard';
import { currentAppLocale } from '../../i18n/locale';
import { localizeStaticUiText } from '../../i18n/messages';

const NOTES_UI_PHRASES = {
  tr: {
    'Save Note': 'Notu kaydet',
    'Study Notes': 'Çalışma Notları',
    'Personal Notebook': 'Kişisel not defteri',
    'Expand study notes': 'Çalışma notlarını genişlet',
    'Collapse study notes': 'Çalışma notlarını daralt',
    'Capture ideas, definitions, and questions while watching...': 'İzlerken fikirleri, tanımları ve soruları yakalayın...',
    'Auto-saved locally': 'Yerel olarak otomatik kaydedildi',
    'unsaved changes': 'kaydedilmemiş değişiklikler',
    'Notes are collapsed. Expand to continue editing your draft.': 'Notlar daraltıldı. Taslağınızı düzenlemeye devam etmek için genişletin.',
  },
  es: { 'Save Note': 'Guardar nota', 'Study Notes': 'Notas de estudio', 'Personal Notebook': 'Cuaderno personal', 'Expand study notes': 'Expandir notas de estudio', 'Collapse study notes': 'Contraer notas de estudio', 'Capture ideas, definitions, and questions while watching...': 'Captura ideas, definiciones y preguntas mientras miras...', 'Auto-saved locally': 'Guardado localmente automáticamente', 'unsaved changes': 'cambios sin guardar', 'Notes are collapsed. Expand to continue editing your draft.': 'Las notas están contraídas. Expándelas para seguir editando el borrador.' },
  fr: { 'Save Note': 'Enregistrer la note', 'Study Notes': 'Notes d’étude', 'Personal Notebook': 'Carnet personnel', 'Expand study notes': 'Développer les notes d’étude', 'Collapse study notes': 'Réduire les notes d’étude', 'Capture ideas, definitions, and questions while watching...': 'Notez idées, définitions et questions pendant le visionnage...', 'Auto-saved locally': 'Enregistré automatiquement en local', 'unsaved changes': 'modifications non enregistrées', 'Notes are collapsed. Expand to continue editing your draft.': 'Les notes sont réduites. Développez-les pour continuer à modifier votre brouillon.' },
  de: { 'Save Note': 'Notiz speichern', 'Study Notes': 'Lernnotizen', 'Personal Notebook': 'Persönliches Notizbuch', 'Expand study notes': 'Lernnotizen erweitern', 'Collapse study notes': 'Lernnotizen einklappen', 'Capture ideas, definitions, and questions while watching...': 'Halte beim Ansehen Ideen, Definitionen und Fragen fest...', 'Auto-saved locally': 'Lokal automatisch gespeichert', 'unsaved changes': 'ungespeicherte Änderungen', 'Notes are collapsed. Expand to continue editing your draft.': 'Notizen sind eingeklappt. Erweitere sie, um deinen Entwurf weiter zu bearbeiten.' },
  it: { 'Save Note': 'Salva nota', 'Study Notes': 'Note di studio', 'Personal Notebook': 'Taccuino personale', 'Expand study notes': 'Espandi note di studio', 'Collapse study notes': 'Comprimi note di studio', 'Capture ideas, definitions, and questions while watching...': 'Annota idee, definizioni e domande durante la visione...', 'Auto-saved locally': 'Salvato automaticamente in locale', 'unsaved changes': 'modifiche non salvate', 'Notes are collapsed. Expand to continue editing your draft.': 'Le note sono compresse. Espandile per continuare a modificare la bozza.' },
  pt: { 'Save Note': 'Salvar nota', 'Study Notes': 'Notas de estudo', 'Personal Notebook': 'Caderno pessoal', 'Expand study notes': 'Expandir notas de estudo', 'Collapse study notes': 'Recolher notas de estudo', 'Capture ideas, definitions, and questions while watching...': 'Registre ideias, definições e perguntas enquanto assiste...', 'Auto-saved locally': 'Salvo automaticamente localmente', 'unsaved changes': 'alterações não salvas', 'Notes are collapsed. Expand to continue editing your draft.': 'As notas estão recolhidas. Expanda para continuar editando o rascunho.' },
  ru: { 'Save Note': 'Сохранить заметку', 'Study Notes': 'Учебные заметки', 'Personal Notebook': 'Личный блокнот', 'Expand study notes': 'Развернуть учебные заметки', 'Collapse study notes': 'Свернуть учебные заметки', 'Capture ideas, definitions, and questions while watching...': 'Записывайте идеи, определения и вопросы во время просмотра...', 'Auto-saved locally': 'Автоматически сохранено локально', 'unsaved changes': 'несохраненные изменения', 'Notes are collapsed. Expand to continue editing your draft.': 'Заметки свернуты. Разверните их, чтобы продолжить редактирование черновика.' },
  ja: { 'Save Note': 'メモを保存', 'Study Notes': '学習メモ', 'Personal Notebook': '個人ノート', 'Expand study notes': '学習メモを展開', 'Collapse study notes': '学習メモを折りたたむ', 'Capture ideas, definitions, and questions while watching...': '視聴中にアイデア、定義、質問を記録...', 'Auto-saved locally': 'ローカルに自動保存済み', 'unsaved changes': '未保存の変更', 'Notes are collapsed. Expand to continue editing your draft.': 'メモは折りたたまれています。展開して下書きの編集を続けます。' },
  ko: { 'Save Note': '메모 저장', 'Study Notes': '학습 메모', 'Personal Notebook': '개인 노트', 'Expand study notes': '학습 메모 펼치기', 'Collapse study notes': '학습 메모 접기', 'Capture ideas, definitions, and questions while watching...': '시청하면서 아이디어, 정의, 질문을 기록하세요...', 'Auto-saved locally': '로컬에 자동 저장됨', 'unsaved changes': '저장되지 않은 변경 사항', 'Notes are collapsed. Expand to continue editing your draft.': '메모가 접혀 있습니다. 초안 편집을 계속하려면 펼치세요.' },
  'zh-CN': { 'Save Note': '保存笔记', 'Study Notes': '学习笔记', 'Personal Notebook': '个人笔记本', 'Expand study notes': '展开学习笔记', 'Collapse study notes': '折叠学习笔记', 'Capture ideas, definitions, and questions while watching...': '观看时记录想法、定义和问题...', 'Auto-saved locally': '已在本地自动保存', 'unsaved changes': '未保存的更改', 'Notes are collapsed. Expand to continue editing your draft.': '笔记已折叠。展开以继续编辑草稿。' },
  ar: { 'Save Note': 'حفظ الملاحظة', 'Study Notes': 'ملاحظات الدراسة', 'Personal Notebook': 'دفتر شخصي', 'Expand study notes': 'توسيع ملاحظات الدراسة', 'Collapse study notes': 'طي ملاحظات الدراسة', 'Capture ideas, definitions, and questions while watching...': 'سجل الأفكار والتعريفات والأسئلة أثناء المشاهدة...', 'Auto-saved locally': 'تم الحفظ تلقائيًا محليًا', 'unsaved changes': 'تغييرات غير محفوظة', 'Notes are collapsed. Expand to continue editing your draft.': 'تم طي الملاحظات. قم بتوسيعها لمتابعة تحرير مسودتك.' },
};

function notesText(locale, text) {
  return NOTES_UI_PHRASES[locale]?.[text] || localizeStaticUiText(locale, text);
}

export default function NotesPanel({
  notes,
  onNotesChange,
  onSave,
  savedAtLabel,
  unsaved = false,
  saveActionLabel = 'Save Note',
  saveHint = '',
  collapsed = false,
  onToggle,
}) {
  const locale = currentAppLocale();
  const uiText = (text) => notesText(locale, text);
  return (
    <SurfaceCard className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="label-sm">{uiText('Study Notes')}</p>
          <h2 className="title-lg mt-1 text-[var(--text-primary)]">{uiText('Personal Notebook')}</h2>
        </div>
        <div className="flex items-center gap-2">
          <NotebookPen size={17} className="text-[var(--text-secondary)]" />
          {typeof onToggle === 'function' && (
            <button
              type="button"
              onClick={onToggle}
              className="focus-ring inline-flex h-8 w-8 items-center justify-center rounded-full token-surface text-[var(--text-secondary)]"
              aria-label={collapsed ? uiText('Expand study notes') : uiText('Collapse study notes')}
            >
              <ChevronDown
                size={15}
                className={`transition ${collapsed ? '-rotate-90' : 'rotate-0'}`}
              />
            </button>
          )}
        </div>
      </div>

      {!collapsed && (
        <>
          <div className="token-glass rounded-2xl p-3">
            <textarea
              value={notes}
              onChange={(event) => onNotesChange(event.target.value)}
              placeholder={uiText('Capture ideas, definitions, and questions while watching...')}
              className="focus-ring min-h-[220px] w-full resize-y rounded-xl border border-[var(--border-subtle)] bg-[color:var(--surface-elevated)] p-3 text-sm leading-relaxed text-[var(--text-primary)]"
            />
          </div>

          <div className="flex items-center justify-between gap-2">
            <p className="inline-flex items-center gap-1 text-xs text-[var(--text-secondary)]">
              <CheckCircle2 size={13} />
              {savedAtLabel || uiText('Auto-saved locally')}
              {unsaved ? ` - ${uiText('unsaved changes')}` : ''}
            </p>
            <Button size="sm" onClick={onSave}>
              {uiText(saveActionLabel)}
            </Button>
          </div>

          {saveHint && (
            <p className="rounded-xl bg-[color:color-mix(in_srgb,var(--surface-muted),transparent_6%)] px-3 py-2 text-xs text-[var(--text-secondary)]">
              {saveHint}
            </p>
          )}
        </>
      )}

      {collapsed && (
        <p className="text-xs text-[var(--text-secondary)]">
          {uiText('Notes are collapsed. Expand to continue editing your draft.')}
        </p>
      )}
    </SurfaceCard>
  );
}
