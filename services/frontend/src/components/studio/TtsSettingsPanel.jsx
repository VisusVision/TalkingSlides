import { useEffect, useMemo, useRef, useState } from 'react';
import { Check, Eye, Plus, RefreshCcw, Save, Trash2, Volume2, Wand2, X } from 'lucide-react';
import {
  fetchTtsPronunciationSuggestions,
  previewTtsAudio,
  previewTtsNormalization,
  updateProjectTtsSettings,
} from '../../api';
import Button from '../ui/Button';

const DEFAULT_TTS_SETTINGS = {
  provider_preference: 'auto',
  normalization_enabled: true,
  normalization_mode: 'loose',
  unknown_word_strategy: 'keep',
  overrides: {
    technical: {},
    abbreviation: {},
    mixed_word: {},
  },
  speech_speed: 1.0,
  volume_gain_db: 0,
  pause_seconds: null,
};

const OVERRIDE_CATEGORIES = [
  { id: 'technical', label: 'Technical' },
  { id: 'abbreviation', label: 'Abbreviation' },
  { id: 'mixed_word', label: 'Mixed word' },
];

function enumValue(value, allowed, fallback) {
  const cleaned = String(value || '').trim().toLowerCase();
  return allowed.includes(cleaned) ? cleaned : fallback;
}

function numberValue(value, fallback, min, max) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
}

function rowId(category, index, term = '') {
  return `${category}-${index}-${String(term).slice(0, 24)}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function mapToRows(category, value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
  return Object.entries(value).map(([term, replacement], index) => ({
    id: rowId(category, index, term),
    term: String(term || ''),
    replacement: String(replacement || ''),
  }));
}

function normalizeSettings(value) {
  const source = value && typeof value === 'object' ? value : {};
  const sourceOverrides = source.overrides && typeof source.overrides === 'object' ? source.overrides : {};

  return {
    provider_preference: enumValue(
      source.provider_preference,
      ['auto', 'xtts_v2', 'gtts'],
      DEFAULT_TTS_SETTINGS.provider_preference,
    ),
    normalization_enabled:
      typeof source.normalization_enabled === 'boolean'
        ? source.normalization_enabled
        : DEFAULT_TTS_SETTINGS.normalization_enabled,
    normalization_mode: enumValue(
      source.normalization_mode,
      ['loose', 'strict'],
      DEFAULT_TTS_SETTINGS.normalization_mode,
    ),
    unknown_word_strategy: enumValue(
      source.unknown_word_strategy,
      ['keep', 'phonetic'],
      DEFAULT_TTS_SETTINGS.unknown_word_strategy,
    ),
    overrides: {
      technical: mapToRows('technical', sourceOverrides.technical),
      abbreviation: mapToRows('abbreviation', sourceOverrides.abbreviation),
      mixed_word: mapToRows('mixed_word', sourceOverrides.mixed_word),
    },
    speech_speed: numberValue(source.speech_speed, DEFAULT_TTS_SETTINGS.speech_speed, 0.5, 1.5),
    volume_gain_db: numberValue(source.volume_gain_db, DEFAULT_TTS_SETTINGS.volume_gain_db, -12, 12),
    pause_seconds:
      source.pause_seconds === null || source.pause_seconds === undefined || source.pause_seconds === ''
        ? null
        : numberValue(source.pause_seconds, DEFAULT_TTS_SETTINGS.pause_seconds, 0, 10),
  };
}

function rowsToOverrideMap(rows) {
  return (rows || []).reduce((acc, row) => {
    const term = String(row.term || '').trim();
    const replacement = String(row.replacement || '').trim();
    if (term && replacement) {
      acc[term] = replacement;
    }
    return acc;
  }, {});
}

function cleanSettings(draft) {
  return {
    provider_preference: enumValue(draft.provider_preference, ['auto', 'xtts_v2', 'gtts'], 'auto'),
    normalization_enabled: Boolean(draft.normalization_enabled),
    normalization_mode: enumValue(draft.normalization_mode, ['loose', 'strict'], 'loose'),
    unknown_word_strategy: enumValue(draft.unknown_word_strategy, ['keep', 'phonetic'], 'keep'),
    overrides: {
      technical: rowsToOverrideMap(draft.overrides.technical),
      abbreviation: rowsToOverrideMap(draft.overrides.abbreviation),
      mixed_word: rowsToOverrideMap(draft.overrides.mixed_word),
    },
    speech_speed: numberValue(draft.speech_speed, 1.0, 0.5, 1.5),
    volume_gain_db: numberValue(draft.volume_gain_db, 0, -12, 12),
    pause_seconds:
      draft.pause_seconds === null || draft.pause_seconds === ''
        ? null
        : numberValue(draft.pause_seconds, null, 0, 10),
  };
}

function sortedMap(value) {
  return Object.keys(value || {})
    .sort((left, right) => left.localeCompare(right))
    .reduce((acc, key) => {
      acc[key] = value[key];
      return acc;
    }, {});
}

function stableSettings(value) {
  return {
    provider_preference: value.provider_preference,
    normalization_enabled: value.normalization_enabled,
    normalization_mode: value.normalization_mode,
    unknown_word_strategy: value.unknown_word_strategy,
    overrides: {
      technical: sortedMap(value.overrides.technical),
      abbreviation: sortedMap(value.overrides.abbreviation),
      mixed_word: sortedMap(value.overrides.mixed_word),
    },
    speech_speed: value.speech_speed,
    volume_gain_db: value.volume_gain_db,
    pause_seconds: value.pause_seconds,
  };
}

function settingsKey(value) {
  return JSON.stringify(stableSettings(value));
}

function canonicalSettings(value) {
  return cleanSettings(normalizeSettings(value));
}

function pageKey(page, index) {
  return String(page?.page_key || page?.id || `page-${index}`);
}

function pagePreviewText(page) {
  return String(page?.narration_text || page?.original_text || '');
}

function uniquePreviewTerms(value) {
  if (!Array.isArray(value)) return [];
  const seen = new Set();
  return value.reduce((acc, item) => {
    const surface =
      typeof item === 'string'
        ? item
        : item?.term || item?.surface || item?.text || item?.value || '';
    const cleaned = String(surface || '').trim();
    const key = cleaned.toLocaleLowerCase();
    if (!cleaned || seen.has(key)) return acc;
    seen.add(key);
    acc.push(cleaned);
    return acc;
  }, []);
}

function isAcronymLikeTerm(term) {
  const cleaned = String(term || '').trim();
  return /^[A-Z0-9][A-Z0-9.+_-]{1,}$/.test(cleaned) && /[A-Z]/.test(cleaned);
}

function overrideCategoryForTerm(term) {
  return isAcronymLikeTerm(term) ? 'abbreviation' : 'mixed_word';
}

function overrideCategoryLabel(categoryId) {
  return OVERRIDE_CATEGORIES.find((category) => category.id === categoryId)?.label || categoryId;
}

function normalizeOverrideCategory(categoryId, fallback = 'mixed_word') {
  return OVERRIDE_CATEGORIES.some((category) => category.id === categoryId) ? categoryId : fallback;
}

function formatRuleDetail(rule) {
  if (!rule || typeof rule !== 'object') return '';
  const term = rule.term || rule.source_term || rule.match || '';
  const replacement = rule.replacement || rule.spoken || rule.value || '';
  const source = rule.source || '';
  const parts = [];
  if (term) parts.push(String(term));
  if (replacement) parts.push(`-> ${replacement}`);
  if (source) parts.push(`source: ${source}`);
  return parts.join(' ');
}

function previewSeedText(project, transcriptPages, source = 'selected', selectedPageKey = '') {
  const pages = Array.isArray(transcriptPages) ? transcriptPages : [];
  const selectedPage = selectedPageKey
    ? pages.find((page, index) => pageKey(page, index) === selectedPageKey)
    : null;
  if (source === 'selected' && selectedPage) {
    return pagePreviewText(selectedPage);
  }
  if (source === 'all') {
    const combined = pages.map(pagePreviewText).filter((value) => value.trim()).join('\n\n');
    if (combined) return combined;
  }
  const firstPage = pages[0] || null;
  return String(
    firstPage?.narration_text ||
      firstPage?.original_text ||
      project?.title ||
      project?.name ||
      '',
  );
}

function FieldLabel({ label, children }) {
  return (
    <label className="block text-sm text-[var(--text-secondary)]">
      {label}
      {children}
    </label>
  );
}

function PreviewValue({ label, value }) {
  if (value === null || value === undefined || value === '') return null;
  return (
    <div>
      <p className="label-sm">{label}</p>
      <p className="mt-1 whitespace-pre-wrap rounded-xl bg-[var(--surface-elevated)] p-3 text-sm text-[var(--text-primary)]">
        {String(value)}
      </p>
    </div>
  );
}

export default function TtsSettingsPanel({
  project,
  transcriptPages = [],
  selectedPageKey = '',
  onProjectUpdated,
  onRerender,
}) {
  const replacementInputRefs = useRef({});
  const [draftSettings, setDraftSettings] = useState(() => normalizeSettings(project?.tts_settings));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [statusMessage, setStatusMessage] = useState('');
  const [focusReplacementRowId, setFocusReplacementRowId] = useState('');
  const [previewSource, setPreviewSource] = useState('selected');
  const [previewLanguage, setPreviewLanguage] = useState('auto');
  const [previewText, setPreviewText] = useState(() => previewSeedText(project, transcriptPages, 'selected', selectedPageKey));
  const [previewResult, setPreviewResult] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [previewAudio, setPreviewAudio] = useState(null);
  const [previewAudioing, setPreviewAudioing] = useState(false);
  const [previewAudioError, setPreviewAudioError] = useState('');
  const [selectedSuggestionTerms, setSelectedSuggestionTerms] = useState([]);
  const [suggestionCards, setSuggestionCards] = useState([]);
  const [suggesting, setSuggesting] = useState(false);
  const [suggestionWarnings, setSuggestionWarnings] = useState([]);
  const [suggestionMessage, setSuggestionMessage] = useState('');
  const [suggestionError, setSuggestionError] = useState('');
  const [suggestionMeta, setSuggestionMeta] = useState(null);

  useEffect(() => {
    setDraftSettings(normalizeSettings(project?.tts_settings));
    setError('');
    setStatusMessage('');
    setPreviewSource('selected');
    setPreviewLanguage('auto');
    setPreviewText(previewSeedText(project, transcriptPages, 'selected', selectedPageKey));
    setPreviewResult(null);
    setPreviewError('');
    setPreviewAudio(null);
    setPreviewAudioError('');
    setFocusReplacementRowId('');
    setSelectedSuggestionTerms([]);
    setSuggestionCards([]);
    setSuggestionWarnings([]);
    setSuggestionMessage('');
    setSuggestionError('');
    setSuggestionMeta(null);
  }, [project?.id]);

  useEffect(() => {
    if (!project?.id || previewText) return;
    setPreviewText(previewSeedText(project, transcriptPages, previewSource, selectedPageKey));
  }, [project, previewSource, previewText, selectedPageKey, transcriptPages]);

  useEffect(() => {
    if (previewSource === 'custom') return;
    setPreviewText(previewSeedText(project, transcriptPages, previewSource, selectedPageKey));
    setPreviewResult(null);
    setPreviewError('');
    setPreviewAudio(null);
    setPreviewAudioError('');
    setSuggestionCards([]);
    setSuggestionWarnings([]);
    setSuggestionMessage('');
    setSuggestionError('');
    setSuggestionMeta(null);
  }, [previewSource, project, selectedPageKey, transcriptPages]);

  const cleanedDraftSettings = useMemo(() => cleanSettings(draftSettings), [draftSettings]);
  const savedSettings = useMemo(() => canonicalSettings(project?.tts_settings), [project?.tts_settings]);
  const hasUnsavedChanges = useMemo(
    () => settingsKey(cleanedDraftSettings) !== settingsKey(savedSettings),
    [cleanedDraftSettings, savedSettings],
  );

  const previewChunks = useMemo(() => {
    if (!Array.isArray(previewResult?.chunks)) return [];
    return previewResult.chunks.filter((chunk) => String(chunk || '').trim());
  }, [previewResult]);

  const previewWarnings = useMemo(() => {
    if (!Array.isArray(previewResult?.warnings)) return [];
    return previewResult.warnings.filter(Boolean);
  }, [previewResult]);

  const unknownTerms = useMemo(() => uniquePreviewTerms(previewResult?.unknown_terms), [previewResult]);
  const ambiguousTerms = useMemo(() => uniquePreviewTerms(previewResult?.ambiguous_terms), [previewResult]);
  const detectedSuggestionTerms = useMemo(() => {
    const seen = new Set();
    return [...unknownTerms, ...ambiguousTerms].filter((term) => {
      const key = term.toLocaleLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [unknownTerms, ambiguousTerms]);
  const detectedSuggestionTermsKey = useMemo(
    () => detectedSuggestionTerms.map((term) => term.toLocaleLowerCase()).join('|'),
    [detectedSuggestionTerms],
  );
  const previewRules = useMemo(() => {
    if (!Array.isArray(previewResult?.tts_normalization_rules_applied)) return [];
    return previewResult.tts_normalization_rules_applied.filter((rule) => rule && typeof rule === 'object');
  }, [previewResult]);

  const overrideCounts = useMemo(() => {
    return OVERRIDE_CATEGORIES.reduce((acc, category) => {
      acc[category.id] = (draftSettings.overrides[category.id] || []).length;
      return acc;
    }, {});
  }, [draftSettings.overrides]);

  useEffect(() => {
    if (!focusReplacementRowId) return;
    const input = replacementInputRefs.current[focusReplacementRowId];
    if (!input) return;
    input.focus();
    input.select?.();
    setFocusReplacementRowId('');
  }, [focusReplacementRowId, draftSettings.overrides]);

  useEffect(() => {
    setSelectedSuggestionTerms(detectedSuggestionTerms);
    setSuggestionCards([]);
    setSuggestionWarnings([]);
    setSuggestionMessage('');
    setSuggestionError('');
    setSuggestionMeta(null);
  }, [detectedSuggestionTerms, detectedSuggestionTermsKey]);

  const updateField = (field, value) => {
    setDraftSettings((prev) => ({ ...prev, [field]: value }));
    setStatusMessage('');
    setError('');
  };

  const updateOverrideRow = (category, rowIdValue, field, value) => {
    setDraftSettings((prev) => ({
      ...prev,
      overrides: {
        ...prev.overrides,
        [category]: (prev.overrides[category] || []).map((row) =>
          row.id === rowIdValue ? { ...row, [field]: value } : row,
        ),
      },
    }));
    setStatusMessage('');
    setError('');
  };

  const addOverrideRow = (category, initialTerm = '', options = {}) => {
    const existingRows = draftSettings.overrides[category] || [];
    const newRow = {
      id: rowId(category, existingRows.length, initialTerm),
      term: String(initialTerm || ''),
      replacement: '',
    };
    setDraftSettings((prev) => ({
      ...prev,
      overrides: {
        ...prev.overrides,
        [category]: [
          ...(prev.overrides[category] || []),
          newRow,
        ],
      },
    }));
    if (options.focusReplacement) {
      setFocusReplacementRowId(newRow.id);
    }
  };

  const deleteOverrideRow = (category, rowIdValue) => {
    setDraftSettings((prev) => ({
      ...prev,
      overrides: {
        ...prev.overrides,
        [category]: (prev.overrides[category] || []).filter((row) => row.id !== rowIdValue),
      },
    }));
  };

  const upsertOverrideFromSuggestion = (category, term, replacement) => {
    const cleanedTerm = String(term || '').trim();
    const cleanedReplacement = String(replacement || '').trim();
    const cleanedCategory = normalizeOverrideCategory(category, overrideCategoryForTerm(cleanedTerm));
    if (!cleanedTerm || !cleanedReplacement) {
      setSuggestionError('Enter a spoken replacement before accepting the suggestion.');
      return false;
    }

    setDraftSettings((prev) => {
      const nextOverrides = { ...prev.overrides };
      let existingRow = null;
      OVERRIDE_CATEGORIES.forEach((overrideCategory) => {
        const rows = nextOverrides[overrideCategory.id] || [];
        const keptRows = [];
        rows.forEach((row) => {
          if (String(row.term || '').trim().toLocaleLowerCase() === cleanedTerm.toLocaleLowerCase()) {
            existingRow = row;
          } else {
            keptRows.push(row);
          }
        });
        nextOverrides[overrideCategory.id] = keptRows;
      });

      nextOverrides[cleanedCategory] = [
        ...(nextOverrides[cleanedCategory] || []),
        {
          id: existingRow?.id || rowId(cleanedCategory, (nextOverrides[cleanedCategory] || []).length, cleanedTerm),
          term: cleanedTerm,
          replacement: cleanedReplacement,
        },
      ];

      return {
        ...prev,
        overrides: nextOverrides,
      };
    });

    setSuggestionError('');
    setError('');
    setStatusMessage(`Added "${cleanedTerm}" to draft overrides. Click Save to persist.`);
    setSuggestionCards((prev) => prev.filter((card) => card.id !== `${cleanedTerm.toLocaleLowerCase()}`));
    return true;
  };

  const updateSuggestionCard = (cardId, field, value) => {
    setSuggestionCards((prev) =>
      prev.map((card) =>
        card.id === cardId
          ? {
              ...card,
              [field]: field === 'category' ? normalizeOverrideCategory(value, card.category) : value,
            }
          : card,
      ),
    );
    setSuggestionError('');
  };

  const toggleSuggestionTerm = (term) => {
    const cleanedTerm = String(term || '').trim();
    if (!cleanedTerm) return;
    setSelectedSuggestionTerms((prev) => {
      const exists = prev.some((item) => item.toLocaleLowerCase() === cleanedTerm.toLocaleLowerCase());
      return exists ? prev.filter((item) => item.toLocaleLowerCase() !== cleanedTerm.toLocaleLowerCase()) : [...prev, cleanedTerm];
    });
    setSuggestionError('');
  };

  const handleSuggestPronunciations = async () => {
    const terms = selectedSuggestionTerms.filter((term) => detectedSuggestionTerms.includes(term));
    if (!detectedSuggestionTerms.length) {
      setSuggestionMessage('No unknown terms need suggestions.');
      return;
    }
    if (!terms.length) {
      setSuggestionError('Select at least one detected term.');
      return;
    }

    setSuggesting(true);
    setSuggestionError('');
    setSuggestionMessage('');
    setSuggestionWarnings([]);
    setSuggestionCards([]);
    setSuggestionMeta(null);

    try {
      const result = await fetchTtsPronunciationSuggestions({
        language: previewResult?.resolved_language || previewResult?.tts_normalization_language || previewLanguage,
        terms,
        context: previewText || previewResult?.original_text || '',
        project_id: project?.id,
      });
      const warnings = Array.isArray(result?.warnings) ? result.warnings.filter(Boolean) : [];
      const cards = Array.isArray(result?.suggestions)
        ? result.suggestions
            .map((suggestion) => {
              const term = String(suggestion?.term || '').trim();
              if (!term) return null;
              const fallbackCategory = overrideCategoryForTerm(term);
              const category = normalizeOverrideCategory(String(suggestion?.category || '').trim(), fallbackCategory);
              return {
                id: term.toLocaleLowerCase(),
                term,
                suggested_spoken: String(suggestion?.suggested_spoken || '').trim(),
                category,
                confidence: String(suggestion?.confidence || '').trim(),
                reason: String(suggestion?.reason || '').trim(),
              };
            })
            .filter((suggestion) => suggestion && suggestion.suggested_spoken)
        : [];

      setSuggestionWarnings(warnings);
      setSuggestionMeta({
        enabled: result?.enabled,
        fallback_used: Boolean(result?.fallback_used),
        provider: result?.provider || '',
      });
      setSuggestionCards(cards);

      if (result?.enabled === false) {
        setSuggestionMessage('LLM suggestions are disabled. You can still add overrides manually.');
      } else if (!cards.length) {
        setSuggestionMessage('No pronunciation suggestions were returned. You can still add overrides manually.');
      }
    } catch (err) {
      setSuggestionError(err.message || 'Failed to fetch pronunciation suggestions.');
    } finally {
      setSuggesting(false);
    }
  };

  const handleAcceptSuggestion = (card) => {
    const accepted = upsertOverrideFromSuggestion(card.category, card.term, card.suggested_spoken);
    if (accepted) {
      setSuggestionCards((prev) => prev.filter((item) => item.id !== card.id));
    }
  };

  const handleIgnoreSuggestion = (cardId) => {
    setSuggestionCards((prev) => prev.filter((card) => card.id !== cardId));
    setSuggestionError('');
  };

  const saveDraftSettings = async ({ successMessage = 'TTS settings saved.' } = {}) => {
    if (!project?.id) return null;
    setSaving(true);
    setError('');
    setStatusMessage('');

    try {
      const updatedProject = await updateProjectTtsSettings(project.id, cleanedDraftSettings);
      setDraftSettings(normalizeSettings(updatedProject?.tts_settings || cleanedDraftSettings));
      onProjectUpdated?.(updatedProject);
      if (successMessage) {
        setStatusMessage(successMessage);
      }
      return updatedProject;
    } catch (err) {
      setError(err.message || 'Failed to update project TTS settings');
      return null;
    } finally {
      setSaving(false);
    }
  };

  const handleSave = async () => {
    await saveDraftSettings();
  };

  const buildPreviewPayload = (text) => ({
    text,
    language: previewLanguage,
    normalization_enabled: cleanedDraftSettings.normalization_enabled,
    normalization_mode: cleanedDraftSettings.normalization_mode,
    unknown_word_strategy: cleanedDraftSettings.unknown_word_strategy,
    provider_preference: cleanedDraftSettings.provider_preference,
    technical_overrides: cleanedDraftSettings.overrides.technical,
    abbreviation_overrides: cleanedDraftSettings.overrides.abbreviation,
    mixed_word_overrides: cleanedDraftSettings.overrides.mixed_word,
  });

  const handlePreview = async () => {
    const text = String(previewText || '').trim();
    if (!text) {
      setPreviewError('Enter text to preview pronunciation.');
      setPreviewResult(null);
      return;
    }

    setPreviewing(true);
    setPreviewError('');
    setPreviewResult(null);
    setPreviewAudio(null);
    setPreviewAudioError('');
    setSuggestionCards([]);
    setSuggestionWarnings([]);
    setSuggestionMessage('');
    setSuggestionError('');
    setSuggestionMeta(null);

    try {
      const result = await previewTtsNormalization(buildPreviewPayload(text));
      setPreviewResult(result);
    } catch (err) {
      setPreviewError(err.message || 'Failed to preview pronunciation.');
    } finally {
      setPreviewing(false);
    }
  };

  const handlePreviewAudio = async () => {
    const text = String(previewText || '').trim();
    if (!text) {
      setPreviewAudioError('Enter text to listen to a preview.');
      setPreviewAudio(null);
      return;
    }

    setPreviewAudioing(true);
    setPreviewAudioError('');
    setPreviewAudio(null);

    try {
      const result = await previewTtsAudio(buildPreviewPayload(text));
      setPreviewAudio(result);
    } catch (err) {
      setPreviewAudioError(err.message || 'Failed to synthesize preview audio.');
    } finally {
      setPreviewAudioing(false);
    }
  };

  const handleAddResolverOverride = (term) => {
    const cleanedTerm = String(term || '').trim();
    if (!cleanedTerm) return;
    const category = overrideCategoryForTerm(cleanedTerm);
    addOverrideRow(category, cleanedTerm, { focusReplacement: true });
    setStatusMessage(
      `Added "${cleanedTerm}" to ${overrideCategoryLabel(category)} overrides. Enter a spoken replacement and save when ready.`,
    );
    setError('');
  };

  const handleRerenderWithSavedSettings = async () => {
    if (!project?.id || !onRerender) return;

    setError('');
    setStatusMessage('');
    const dirtyBeforeRerender = hasUnsavedChanges;
    let projectForRerender = project;

    if (dirtyBeforeRerender) {
      const updatedProject = await saveDraftSettings({ successMessage: '' });
      if (!updatedProject) return;
      projectForRerender = updatedProject;
    }

    const started = await onRerender(projectForRerender);
    if (started === false) {
      setStatusMessage(dirtyBeforeRerender ? 'Settings saved.' : '');
      return;
    }
    setStatusMessage(dirtyBeforeRerender ? 'Settings saved. Rerender started.' : 'Rerender started.');
  };

  const renderResolverTermSection = (title, description, terms) => {
    if (!terms.length) return null;
    return (
      <div className="space-y-2 rounded-xl bg-[var(--surface-elevated)] p-3">
        <div>
          <p className="font-semibold text-[var(--text-primary)]">{title}</p>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">{description}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {terms.map((term) => {
            const category = overrideCategoryForTerm(term);
            return (
              <div
                key={`${title}-${term}`}
                className="flex min-w-0 max-w-full flex-wrap items-center gap-2 rounded-xl bg-[var(--surface-container-high)] px-3 py-2"
              >
                <span className="break-all text-sm font-semibold text-[var(--text-primary)]">{term}</span>
                <span className="text-xs text-[var(--text-secondary)]">
                  {overrideCategoryLabel(category)}
                </span>
                <Button size="sm" variant="secondary" onClick={() => handleAddResolverOverride(term)}>
                  <Plus size={14} />
                  <span>Add override</span>
                </Button>
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  const renderSuggestionPanel = () => (
    <div className="space-y-3 rounded-xl bg-[var(--surface-elevated)] p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-semibold text-[var(--text-primary)]">Pronunciation suggestions</p>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">
            Optional suggestions become draft overrides only when you accept them.
          </p>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">
            LLM suggestions are optional and controlled by server configuration. Manual overrides always work.
          </p>
        </div>
        <Button
          size="sm"
          variant="secondary"
          onClick={handleSuggestPronunciations}
          disabled={suggesting || !detectedSuggestionTerms.length || !selectedSuggestionTerms.length}
        >
          <Wand2 size={14} />
          <span>{suggesting ? 'Suggesting...' : 'Suggest pronunciations'}</span>
        </Button>
      </div>

      {!detectedSuggestionTerms.length ? (
        <p className="rounded-xl bg-[var(--surface-container-high)] px-3 py-2 text-xs font-semibold text-[var(--text-secondary)]">
          No unknown terms need suggestions.
        </p>
      ) : (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            {detectedSuggestionTerms.map((term) => {
              const checked = selectedSuggestionTerms.some((item) => item.toLocaleLowerCase() === term.toLocaleLowerCase());
              return (
                <label
                  key={`suggest-select-${term}`}
                  className="inline-flex min-w-0 max-w-full items-center gap-2 rounded-xl bg-[var(--surface-container-high)] px-3 py-2 text-xs text-[var(--text-secondary)]"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleSuggestionTerm(term)}
                  />
                  <span className="break-all font-semibold text-[var(--text-primary)]">{term}</span>
                </label>
              );
            })}
          </div>
          {!selectedSuggestionTerms.length && (
            <p className="text-xs text-[var(--text-secondary)]">Select at least one term to request suggestions.</p>
          )}
        </div>
      )}

      {suggestionMessage && (
        <p className="rounded-xl bg-[color:var(--status-warning-bg)] px-3 py-2 text-xs font-semibold text-[color:var(--status-warning-fg)]">
          {suggestionMessage}
        </p>
      )}

      {suggestionError && (
        <p className="rounded-xl bg-[color:var(--feedback-danger-bg)] px-3 py-2 text-xs font-semibold text-[color:var(--feedback-danger-fg)]">
          {suggestionError}
        </p>
      )}

      {suggestionWarnings.length > 0 && (
        <ul className="space-y-1 text-xs text-[var(--text-secondary)]">
          {suggestionWarnings.map((warning, index) => (
            <li key={`${index}-${warning}`} className="rounded-xl bg-[var(--surface-container-high)] px-3 py-2">
              {warning}
            </li>
          ))}
        </ul>
      )}

      {suggestionCards.length > 0 && (
        <div className="space-y-2">
          {suggestionCards.map((card) => (
            <div key={card.id} className="space-y-3 rounded-xl bg-[var(--surface-container-high)] p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="break-all text-sm font-semibold text-[var(--text-primary)]">{card.term}</span>
                {card.confidence && (
                  <span className="rounded-full bg-[var(--surface-elevated)] px-2 py-1 text-xs text-[var(--text-secondary)]">
                    {card.confidence}
                  </span>
                )}
              </div>
              {card.reason && <p className="text-xs leading-relaxed text-[var(--text-secondary)]">{card.reason}</p>}
              <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_minmax(150px,220px)]">
                <FieldLabel label="Suggested spoken form">
                  <input
                    value={card.suggested_spoken}
                    onChange={(event) => updateSuggestionCard(card.id, 'suggested_spoken', event.target.value)}
                    className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
                  />
                </FieldLabel>
                <FieldLabel label="Override category">
                  <select
                    value={card.category}
                    onChange={(event) => updateSuggestionCard(card.id, 'category', event.target.value)}
                    className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
                  >
                    {OVERRIDE_CATEGORIES.map((category) => (
                      <option key={category.id} value={category.id}>
                        {category.label}
                      </option>
                    ))}
                  </select>
                </FieldLabel>
              </div>
              <div className="flex flex-wrap justify-end gap-2">
                <Button size="sm" variant="secondary" onClick={() => handleAcceptSuggestion(card)}>
                  <Check size={14} />
                  <span>Accept as override</span>
                </Button>
                <Button size="sm" variant="ghost" onClick={() => handleIgnoreSuggestion(card.id)}>
                  <X size={14} />
                  <span>Ignore</span>
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );

  if (!project) {
    return <p className="text-sm text-[var(--text-secondary)]">Select a lesson to edit TTS settings.</p>;
  }

  return (
    <div className="space-y-5">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <p className="title-lg text-[var(--text-primary)]">TTS Settings</p>
          {hasUnsavedChanges && (
            <span className="rounded-full bg-[color:var(--status-warning-bg)] px-2.5 py-1 text-xs font-semibold text-[color:var(--status-warning-fg)]">
              Unsaved changes
            </span>
          )}
        </div>
        <p className="mt-1 text-sm text-[var(--text-secondary)]">
          Save project pronunciation preferences before rerendering. Captions and transcript text stay original; these settings affect spoken TTS after rerender.
        </p>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <FieldLabel label="Provider preference">
          <select
            value={draftSettings.provider_preference}
            onChange={(event) => updateField('provider_preference', event.target.value)}
            className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
          >
            <option value="auto">Auto</option>
            <option value="xtts_v2">XTTS v2</option>
            <option value="gtts">gTTS</option>
          </select>
        </FieldLabel>

        <FieldLabel label="Normalization mode">
          <select
            value={draftSettings.normalization_mode}
            onChange={(event) => updateField('normalization_mode', event.target.value)}
            className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
          >
            <option value="loose">Loose</option>
            <option value="strict">Strict</option>
          </select>
        </FieldLabel>

        <FieldLabel label="Unknown word strategy">
          <select
            value={draftSettings.unknown_word_strategy}
            onChange={(event) => updateField('unknown_word_strategy', event.target.value)}
            className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
          >
            <option value="keep">Keep</option>
            <option value="phonetic">Phonetic</option>
          </select>
        </FieldLabel>

        <label className="mt-6 inline-flex items-center gap-2 rounded-xl px-2 py-1 text-sm text-[var(--text-secondary)]">
          <input
            type="checkbox"
            checked={draftSettings.normalization_enabled}
            onChange={(event) => updateField('normalization_enabled', event.target.checked)}
          />
          <span>Enable normalization</span>
        </label>
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <FieldLabel label={`Speech speed (${Number(draftSettings.speech_speed).toFixed(2)}x)`}>
          <input
            type="range"
            min="0.5"
            max="1.5"
            step="0.05"
            value={draftSettings.speech_speed}
            onChange={(event) => updateField('speech_speed', numberValue(event.target.value, 1.0, 0.5, 1.5))}
            className="mt-2 w-full"
          />
          <input
            type="number"
            min="0.5"
            max="1.5"
            step="0.05"
            value={draftSettings.speech_speed}
            onChange={(event) => updateField('speech_speed', numberValue(event.target.value, 1.0, 0.5, 1.5))}
            className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
          />
        </FieldLabel>

        <FieldLabel label={`Volume gain (${draftSettings.volume_gain_db} dB)`}>
          <input
            type="range"
            min="-12"
            max="12"
            step="1"
            value={draftSettings.volume_gain_db}
            onChange={(event) => updateField('volume_gain_db', numberValue(event.target.value, 0, -12, 12))}
            className="mt-2 w-full"
          />
          <input
            type="number"
            min="-12"
            max="12"
            step="1"
            value={draftSettings.volume_gain_db}
            onChange={(event) => updateField('volume_gain_db', numberValue(event.target.value, 0, -12, 12))}
            className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
          />
        </FieldLabel>

        <FieldLabel label="Pause seconds">
          <input
            type="number"
            min="0"
            max="10"
            step="0.1"
            value={draftSettings.pause_seconds ?? ''}
            onChange={(event) =>
              updateField(
                'pause_seconds',
                event.target.value === '' ? null : numberValue(event.target.value, null, 0, 10),
              )
            }
            placeholder="Default"
            className="focus-ring mt-7 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
          />
        </FieldLabel>
      </div>

      <div className="rounded-2xl token-surface p-3 text-xs leading-relaxed text-[var(--text-secondary)]">
        <p>Provider preference is advisory only and does not disable XTTS -&gt; gTTS -&gt; silent fallback.</p>
        <p className="mt-1">Phonetic is persisted but is not a full phonetic engine yet.</p>
      </div>

      <div className="space-y-4">
        <div>
          <p className="title-lg text-[var(--text-primary)]">Pronunciation Overrides</p>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Manual replacements are saved with the project and applied to spoken TTS text after rerender.
          </p>
        </div>

        {OVERRIDE_CATEGORIES.map((category) => {
          const rows = draftSettings.overrides[category.id] || [];
          return (
            <section key={category.id} className="space-y-3 rounded-2xl token-surface p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="font-semibold text-[var(--text-primary)]">{category.label}</p>
                  <p className="text-xs text-[var(--text-secondary)]">{overrideCounts[category.id]} row(s)</p>
                </div>
                <Button size="sm" variant="secondary" onClick={() => addOverrideRow(category.id)}>
                  <Plus size={14} />
                  <span>Add Row</span>
                </Button>
              </div>

              {rows.length === 0 ? (
                <p className="text-sm text-[var(--text-secondary)]">No overrides in this category.</p>
              ) : (
                <div className="space-y-2">
                  {rows.map((row) => (
                    <div key={row.id} className="grid gap-2 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
                      <input
                        value={row.term}
                        onChange={(event) => updateOverrideRow(category.id, row.id, 'term', event.target.value)}
                        placeholder="Term"
                        className="focus-ring h-10 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
                      />
                      <input
                        ref={(node) => {
                          if (node) {
                            replacementInputRefs.current[row.id] = node;
                          } else {
                            delete replacementInputRefs.current[row.id];
                          }
                        }}
                        value={row.replacement}
                        onChange={(event) => updateOverrideRow(category.id, row.id, 'replacement', event.target.value)}
                        placeholder="Spoken replacement"
                        className="focus-ring h-10 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
                      />
                      <Button size="sm" variant="ghost" onClick={() => deleteOverrideRow(category.id, row.id)}>
                        <Trash2 size={14} />
                        <span>Delete</span>
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </section>
          );
        })}
      </div>

      <section className="space-y-3 rounded-2xl token-surface p-3">
        <div>
          <p className="title-lg text-[var(--text-primary)]">Pronunciation Preview</p>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Preview affects spoken TTS only; captions/transcripts remain original.
          </p>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">
            Manual overrides are protected from downstream re-normalization during preview.
          </p>
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <FieldLabel label="Preview source">
            <select
              value={previewSource}
              onChange={(event) => {
                const nextSource = event.target.value;
                setPreviewSource(nextSource);
                if (nextSource !== 'custom') {
                  setPreviewText(previewSeedText(project, transcriptPages, nextSource, selectedPageKey));
                  setPreviewResult(null);
                  setPreviewError('');
                  setPreviewAudio(null);
                  setPreviewAudioError('');
                }
              }}
              className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
            >
              <option value="selected">Selected slide</option>
              <option value="first">First slide</option>
              <option value="all">All slides combined</option>
              <option value="custom">Custom text</option>
            </select>
          </FieldLabel>

          <FieldLabel label="Preview language">
            <select
              value={previewLanguage}
              onChange={(event) => {
                setPreviewLanguage(event.target.value);
                setPreviewResult(null);
                setPreviewError('');
                setPreviewAudio(null);
                setPreviewAudioError('');
                setSuggestionCards([]);
                setSuggestionWarnings([]);
                setSuggestionMessage('');
                setSuggestionError('');
                setSuggestionMeta(null);
              }}
              className="focus-ring mt-1 h-10 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
            >
              <option value="auto">Auto</option>
              <option value="tr">Turkish</option>
              <option value="en">English</option>
            </select>
          </FieldLabel>
        </div>

        <textarea
          value={previewText}
          onChange={(event) => {
            setPreviewSource('custom');
            setPreviewText(event.target.value);
            setPreviewError('');
            setPreviewAudio(null);
            setPreviewAudioError('');
            setSuggestionCards([]);
            setSuggestionWarnings([]);
            setSuggestionMessage('');
            setSuggestionError('');
            setSuggestionMeta(null);
          }}
          placeholder="Enter a sentence to preview spoken pronunciation..."
          className="focus-ring min-h-[120px] w-full resize-y rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-3 text-sm leading-relaxed text-[var(--text-primary)]"
        />

        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-[var(--text-secondary)]">
            Preview uses the current draft values, including unsaved override rows.
          </p>
          <Button variant="secondary" onClick={handlePreview} disabled={previewing || !String(previewText || '').trim()}>
            <Eye size={16} />
            <span>{previewing ? 'Previewing...' : 'Preview pronunciation'}</span>
          </Button>
          <Button variant="secondary" onClick={handlePreviewAudio} disabled={previewAudioing || !String(previewText || '').trim()}>
            <Volume2 size={16} />
            <span>{previewAudioing ? 'Preparing audio...' : 'Listen preview'}</span>
          </Button>
        </div>

        {previewError && (
          <div className="rounded-2xl bg-[color:var(--feedback-danger-bg)] p-3 text-sm text-[color:var(--feedback-danger-fg)]">
            {previewError}
          </div>
        )}

        {previewAudioError && (
          <div className="rounded-2xl bg-[color:var(--feedback-danger-bg)] p-3 text-sm text-[color:var(--feedback-danger-fg)]">
            {previewAudioError}
          </div>
        )}

        {previewAudio?.audio_data_url && (
          <div className="space-y-2 rounded-2xl bg-[var(--surface-container-high)] p-3">
            <audio controls src={previewAudio.audio_data_url} className="w-full" />
            <p className="text-xs text-[var(--text-secondary)]">
              Preview audio uses the current preview text and settings only. Provider: {previewAudio.provider || 'unknown'}
              {previewAudio.resolved_language ? `, language: ${previewAudio.resolved_language}` : ''}
              {previewAudio.fallback_used ? ', fallback audio used' : ''}.
            </p>
          </div>
        )}

        {previewResult && (
          <div className="space-y-3 rounded-2xl bg-[var(--surface-container-high)] p-3">
            {previewResult.fallback_used && (
              <p className="rounded-xl bg-[color:var(--status-warning-bg)] px-3 py-2 text-xs font-semibold text-[color:var(--status-warning-fg)]">
                Preview used fail-open fallback.
              </p>
            )}
            <PreviewValue label="Original text" value={previewResult.original_text} />
            <PreviewValue label="Normalized text" value={previewResult.normalized_text} />
            <PreviewValue label="Spoken text" value={previewResult.spoken_text || previewResult.used_text} />

            {renderSuggestionPanel()}

            <div className="space-y-3">
              {unknownTerms.length === 0 && ambiguousTerms.length === 0 ? (
                <p className="rounded-xl bg-[color:var(--status-success-bg)] px-3 py-2 text-xs font-semibold text-[color:var(--status-success-fg)]">
                  No unknown terms detected.
                </p>
              ) : (
                <>
                  <div className="space-y-1 text-xs leading-relaxed text-[var(--text-secondary)]">
                    <p>Unknown terms are not changed automatically. Add an override if you want to control pronunciation.</p>
                    <p>Ambiguous terms are left unchanged until you choose an override.</p>
                    <p>Preview affects spoken TTS only; captions/transcripts remain original.</p>
                  </div>
                  {renderResolverTermSection(
                    'Unknown terms detected',
                    'These terms are left unchanged unless you add and save a manual pronunciation override.',
                    unknownTerms,
                  )}
                  {renderResolverTermSection(
                    'Ambiguous terms',
                    'Ambiguous terms are left unchanged until you choose an override.',
                    ambiguousTerms,
                  )}
                </>
              )}
            </div>

            <details className="rounded-xl bg-[var(--surface-elevated)] px-3 py-2 text-sm text-[var(--text-secondary)]">
              <summary className="cursor-pointer font-semibold text-[var(--text-primary)]">Technical details</summary>
              <div className="mt-3 space-y-3">
                <PreviewValue label="Resolved language" value={previewResult.resolved_language || previewResult.tts_normalization_language} />
                {suggestionMeta && (
                  <div>
                    <p className="label-sm">Suggestion endpoint</p>
                    <p className="mt-1 rounded-xl bg-[var(--surface-container-high)] px-3 py-2 text-sm text-[var(--text-secondary)]">
                      Enabled: {suggestionMeta.enabled === false ? 'no' : 'yes'}
                      {suggestionMeta.provider ? `, provider: ${suggestionMeta.provider}` : ''}
                      {suggestionMeta.fallback_used ? ', fail-open response' : ''}
                    </p>
                  </div>
                )}

                {previewRules.length > 0 && (
                  <div>
                    <p className="label-sm">Applied resolver rules</p>
                    <ul className="mt-1 space-y-1 text-sm text-[var(--text-secondary)]">
                      {previewRules.map((rule, index) => {
                        const detail = formatRuleDetail(rule);
                        return (
                          <li
                            key={`${index}-${rule.rule || 'rule'}-${detail}`}
                            className="rounded-xl bg-[var(--surface-container-high)] px-3 py-2"
                          >
                            <span className="font-semibold text-[var(--text-primary)]">
                              {rule.rule || 'rule'}
                            </span>
                            {detail ? <span> {detail}</span> : null}
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                )}

                {previewChunks.length > 0 && (
                  <div>
                    <p className="label-sm">Chunks</p>
                    <ol className="mt-1 space-y-1 text-sm text-[var(--text-secondary)]">
                      {previewChunks.map((chunk, index) => (
                        <li key={`${index}-${chunk}`} className="rounded-xl bg-[var(--surface-container-high)] px-3 py-2">
                          {chunk}
                        </li>
                      ))}
                    </ol>
                  </div>
                )}

                {previewWarnings.length > 0 && (
                  <div>
                    <p className="label-sm">Warnings</p>
                    <ul className="mt-1 space-y-1 text-sm text-[var(--text-secondary)]">
                      {previewWarnings.map((warning, index) => (
                        <li key={`${index}-${warning}`} className="rounded-xl bg-[var(--surface-container-high)] px-3 py-2">
                          {warning}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {previewResult.error && (
                  <PreviewValue label="Preview error" value={previewResult.error} />
                )}
              </div>
            </details>
          </div>
        )}
      </section>

      {(error || statusMessage) && (
        <div
          className={`rounded-2xl p-3 text-sm ${
            error
              ? 'bg-[color:var(--feedback-danger-bg)] text-[color:var(--feedback-danger-fg)]'
              : 'bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]'
          }`}
        >
          {error || statusMessage}
        </div>
      )}

      <div className="flex flex-wrap justify-end gap-2">
        {onRerender && (
          <Button variant="secondary" onClick={handleRerenderWithSavedSettings} disabled={saving || !project?.id}>
            <RefreshCcw size={16} />
            <span>Rerender with saved settings</span>
          </Button>
        )}
        <Button onClick={handleSave} disabled={saving || !project?.id}>
          <Save size={16} />
          <span>{saving ? 'Saving...' : 'Save TTS Settings'}</span>
        </Button>
      </div>
    </div>
  );
}
