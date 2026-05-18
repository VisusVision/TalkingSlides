import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  LoaderCircle,
  Merge,
  Pencil,
  RefreshCcw,
  RotateCcw,
  Save,
  Scissors,
  Sparkles,
  Trash2,
  Undo2,
} from 'lucide-react';
import { fetchJobStatus, fetchProjectTranscript, transcriptPageAction, updateProjectTranscript } from '../../api';
import Button from '../ui/Button';

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 5 * 60 * 1000;

function textValue(value) {
  return value === null || value === undefined ? '' : String(value);
}

function narrationValue(page) {
  if (page && Object.prototype.hasOwnProperty.call(page, 'narration_text')) {
    return textValue(page.narration_text);
  }
  return textValue(page?.original_text);
}

function displayValue(page) {
  if (page && Object.prototype.hasOwnProperty.call(page, 'original_text')) {
    return textValue(page.original_text);
  }
  return narrationValue(page);
}

function escapeHtml(value) {
  return textValue(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function narrationToHtml(value) {
  return escapeHtml(value).replace(/\r\n|\r|\n/g, '<br />');
}

function narrationToEditorDocument(value, html) {
  const paragraphs = textValue(value)
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .split('\n')
    .map((text, index) => ({ index, text }));

  return {
    version: 1,
    html,
    paragraphs,
  };
}

function editorTextFlags(page) {
  const flags = page?.editor_document?.text && typeof page.editor_document.text === 'object'
    ? page.editor_document.text
    : {};
  const displayText = displayValue(page).replace(/\s+/g, ' ').trim();
  const narrationText = narrationValue(page).replace(/\s+/g, ' ').trim();
  return {
    narration_customized: Boolean(
      flags.narration_customized
        || (narrationText && displayText !== narrationText),
    ),
    display_text_customized: Boolean(flags.display_text_customized),
  };
}

function displayToEditorDocument(value, html, flags = {}) {
  return {
    ...narrationToEditorDocument(value, html),
    text: {
      narration_customized: Boolean(flags.narration_customized),
      display_text_customized: Boolean(flags.display_text_customized),
    },
  };
}

function clonePage(page, index) {
  return {
    id: page?.id,
    page_key: page?.page_key,
    order: page?.order ?? index,
    source_slide_index: page?.source_slide_index,
    split_index: page?.split_index,
    original_text: textValue(page?.original_text),
    narration_text: narrationValue(page),
    rich_text_html: textValue(page?.rich_text_html),
    editor_document:
      page?.editor_document && typeof page.editor_document === 'object'
        ? { ...page.editor_document }
        : {},
    subtitle_chunks: Array.isArray(page?.subtitle_chunks) ? [...page.subtitle_chunks] : [],
    whiteboard_mode: Boolean(page?.whiteboard_mode),
  };
}

function normalizePages(pages) {
  return Array.isArray(pages) ? pages.map((page, index) => clonePage(page, index)) : [];
}

function pageKey(page, index) {
  return String(page?.page_key || page?.id || `page-${index}`);
}

function pageLabel(page, index) {
  return page?.source_slide_index !== undefined && page?.source_slide_index !== null
    ? `Slide ${Number(page.source_slide_index) + 1}`
    : `Slide ${index + 1}`;
}

function pageDescriptor(page, index) {
  const key = page?.page_key ? ` (${page.page_key})` : '';
  return `${pageLabel(page, index)}${key}`;
}

function editableSignature(page) {
  const flags = editorTextFlags(page);
  return JSON.stringify({
    original_text: textValue(page?.original_text),
    narration_text: textValue(page?.narration_text),
    whiteboard_mode: Boolean(page?.whiteboard_mode),
    narration_customized: flags.narration_customized,
    display_text_customized: flags.display_text_customized,
  });
}

function hasDoubleBlankLine(value) {
  return /\n\s*\n/.test(textValue(value).replace(/\r\n/g, '\n').replace(/\r/g, '\n'));
}

function splitByBlankLines(value) {
  return textValue(value)
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .split(/\n\s*\n+/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function diffLabelForPage(page, index, dirtyPageIndexes) {
  const narration = textValue(page?.narration_text);
  if (!narration.trim()) return 'empty';
  if (hasDoubleBlankLine(narration)) return 'split candidate';
  if (dirtyPageIndexes.has(index)) return 'edited';
  return 'unchanged';
}

function buildPayloadPage(page) {
  const narrationText = textValue(page?.narration_text);
  const displayText = displayValue(page);
  const html = narrationToHtml(displayText);
  const flags = editorTextFlags(page);
  const payload = {
    original_text: displayText,
    narration_text: narrationText,
    rich_text_html: html,
    editor_document: displayToEditorDocument(displayText, html, flags),
    whiteboard_mode: Boolean(page?.whiteboard_mode),
  };

  for (const key of ['id', 'page_key', 'order', 'source_slide_index', 'split_index']) {
    if (page?.[key] !== undefined && page?.[key] !== null && page?.[key] !== '') {
      payload[key] = page[key];
    }
  }

  return payload;
}

async function refetchTranscriptPages(projectId) {
  const payload = await fetchProjectTranscript(projectId);
  return Array.isArray(payload?.pages) ? payload.pages : [];
}

function jobStatusLabel(payload) {
  if (!payload) return '';
  if (payload.notfound) return 'Job not found';
  const raw = textValue(payload.status).trim();
  const progress = Number(payload.progress);
  if (Number.isFinite(progress) && raw) return `${raw} (${progress}%)`;
  return raw || 'Queued';
}

function isTerminalStatus(payload) {
  if (!payload) return false;
  if (payload.notfound) return true;
  const status = textValue(payload.status).trim().toLowerCase();
  return ['done', 'ready', 'success', 'succeeded', 'failed', 'error'].includes(status);
}

function isFailureStatus(payload) {
  if (!payload) return false;
  if (payload.notfound) return true;
  const status = textValue(payload.status).trim().toLowerCase();
  return ['failed', 'error'].includes(status);
}

function wait(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function findPageIndexByIdOrKey(pages, page) {
  if (!Array.isArray(pages) || !page) return -1;
  return pages.findIndex((candidate) => {
    if (page.id && candidate?.id === page.id) return true;
    if (page.page_key && candidate?.page_key === page.page_key) return true;
    return false;
  });
}

function actionLabel(action) {
  return {
    split_page: 'Split page',
    merge_with_next: 'Merge with next',
    merge_with_previous: 'Merge with previous',
    reorder_pages: 'Reorder pages',
    delete_page: 'Delete page',
    restore_page: 'Restore page',
  }[action] || 'Page action';
}

function suggestionDraftNarrationText(suggestion = {}) {
  return textValue(suggestion.draft_narration || suggestion.copy_text).trim();
}

const TranscriptEditorPanel = forwardRef(function TranscriptEditorPanel({
  project,
  pages,
  loading = false,
  selectedPageKey = '',
  selectedPageIndex = 0,
  moderationPageWarnings = {},
  focusMode = false,
  showLocalActions = true,
  onSelectPage,
  onPagesUpdated,
  onProjectRefresh,
  onModerationUpdated,
  onDraftStatusChange,
  onJobStatusChange,
}, ref) {
  const [draftPages, setDraftPages] = useState([]);
  const [saving, setSaving] = useState(false);
  const [rerendering, setRerendering] = useState(false);
  const [error, setError] = useState('');
  const [statusMessage, setStatusMessage] = useState('');
  const [jobStatus, setJobStatus] = useState(null);
  const [pollingStartedAt, setPollingStartedAt] = useState(null);
  const [actioning, setActioning] = useState(false);
  const [rerenderAfterAction, setRerenderAfterAction] = useState(false);
  const [deletedPages, setDeletedPages] = useState([]);
  const [pendingConfirmation, setPendingConfirmation] = useState(null);
  const [displayEditKeys, setDisplayEditKeys] = useState({});
  const [aiAppliedDraftsByPageKey, setAiAppliedDraftsByPageKey] = useState({});
  const mountedRef = useRef(false);
  const lastProjectIdRef = useRef(null);
  const pageRefs = useRef({});

  const sourcePages = useMemo(() => normalizePages(pages), [pages]);
  const activePageKey = selectedPageKey || pageKey(draftPages[selectedPageIndex], selectedPageIndex || 0);
  const selectedDraftIndex = useMemo(() => {
    const indexByKey = draftPages.findIndex((page, index) => pageKey(page, index) === activePageKey);
    if (indexByKey >= 0) return indexByKey;
    if (selectedPageIndex >= 0 && selectedPageIndex < draftPages.length) return selectedPageIndex;
    return draftPages.length ? 0 : -1;
  }, [activePageKey, draftPages, selectedPageIndex]);
  const selectedDraftPage = selectedDraftIndex >= 0 ? draftPages[selectedDraftIndex] : null;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    setDraftPages(normalizePages(pages));
    if (lastProjectIdRef.current !== project?.id) {
      lastProjectIdRef.current = project?.id;
      setError('');
      setStatusMessage('');
      setJobStatus(null);
      setPollingStartedAt(null);
      setDeletedPages([]);
      setPendingConfirmation(null);
      setDisplayEditKeys({});
      setAiAppliedDraftsByPageKey({});
    }
  }, [project?.id, pages]);

  const dirtyPageIndexes = useMemo(() => {
    const dirty = new Set();
    draftPages.forEach((page, index) => {
      const source = sourcePages[index];
      if (!source || editableSignature(page) !== editableSignature(source)) {
        dirty.add(index);
      }
    });
    return dirty;
  }, [draftPages, sourcePages]);

  const isDirty = dirtyPageIndexes.size > 0 || draftPages.length !== sourcePages.length;

  const draftStatusByKey = useMemo(() => {
    return draftPages.reduce((acc, page, index) => {
      const key = pageKey(page, index);
      acc[key] = {
        index,
        status: diffLabelForPage(page, index, dirtyPageIndexes),
        dirty: dirtyPageIndexes.has(index),
        display_text: displayValue(page),
        narration_text: textValue(page?.narration_text),
      };
      return acc;
    }, {});
  }, [dirtyPageIndexes, draftPages]);

  useEffect(() => {
    onDraftStatusChange?.(draftStatusByKey);
  }, [draftStatusByKey, onDraftStatusChange]);

  useEffect(() => {
    if (!activePageKey) return;
    const node = pageRefs.current[activePageKey];
    if (node) {
      node.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }, [activePageKey]);

  useEffect(() => {
    onJobStatusChange?.(jobStatus);
  }, [jobStatus, onJobStatusChange]);

  const updateDraftPage = useCallback((index, patch) => {
    setDraftPages((current) =>
      current.map((page, pageIndex) => (pageIndex === index ? { ...page, ...patch } : page)),
    );
  }, []);

  const updateDisplayText = useCallback((index, nextText) => {
    setDraftPages((current) =>
      current.map((page, pageIndex) => {
        if (pageIndex !== index) return page;
        const previousFlags = editorTextFlags(page);
        const flags = {
          ...previousFlags,
          display_text_customized: true,
        };
        const nextPage = {
          ...page,
          original_text: nextText,
          editor_document: {
            ...(page.editor_document || {}),
            text: flags,
          },
        };
        if (!previousFlags.narration_customized) {
          nextPage.narration_text = nextText;
        }
        return nextPage;
      }),
    );
  }, []);

  const updateNarrationText = useCallback((index, nextText) => {
    const key = pageKey(draftPages[index], index);
    if (key) {
      setAiAppliedDraftsByPageKey((current) => {
        if (!current[key]) return current;
        const next = { ...current };
        delete next[key];
        return next;
      });
    }
    setDraftPages((current) =>
      current.map((page, pageIndex) => {
        if (pageIndex !== index) return page;
        const flags = {
          ...editorTextFlags(page),
          narration_customized: true,
        };
        return {
          ...page,
          narration_text: nextText,
          editor_document: {
            ...(page.editor_document || {}),
            text: flags,
          },
        };
      }),
    );
  }, [draftPages]);

  const toggleDisplayEdit = useCallback((key) => {
    setDisplayEditKeys((current) => ({
      ...current,
      [key]: !current[key],
    }));
  }, []);

  const selectFromPages = useCallback(
    (nextPages, selectPage, fallbackIndex = 0) => {
      if (!Array.isArray(nextPages) || !nextPages.length) return;
      const index = findPageIndexByIdOrKey(nextPages, selectPage);
      const nextIndex = index >= 0 ? index : Math.min(Math.max(fallbackIndex, 0), nextPages.length - 1);
      onSelectPage?.(nextPages[nextIndex], nextIndex);
    },
    [onSelectPage],
  );

  const resetDraft = useCallback(() => {
    setDraftPages(normalizePages(pages));
    setError('');
    setStatusMessage('Discarded unsaved transcript edits.');
    setJobStatus(null);
    setPollingStartedAt(null);
    setDisplayEditKeys({});
    setAiAppliedDraftsByPageKey({});
  }, [pages]);

  const pageDiffLabel = useCallback(
    (page, index) => diffLabelForPage(page, index, dirtyPageIndexes),
    [dirtyPageIndexes],
  );

  const pollRerenderJob = useCallback(
    async (jobId) => {
      const startedAt = Date.now();
      setPollingStartedAt(startedAt);
      setRerendering(true);

      try {
        while (Date.now() - startedAt <= POLL_TIMEOUT_MS) {
          await wait(POLL_INTERVAL_MS);
          if (!mountedRef.current) return;

          const payload = await fetchJobStatus(project.id, jobId);
          if (!mountedRef.current) return;
          setJobStatus(payload);

          if (isTerminalStatus(payload)) {
            if (isFailureStatus(payload)) {
              setError(payload?.error_message || 'Rerender job failed.');
            } else {
              if (Array.isArray(payload?.transcript_pages)) {
                onPagesUpdated?.(payload.transcript_pages);
              }
              setStatusMessage('Rerender completed.');
            }
            if (onProjectRefresh) {
              await onProjectRefresh();
            }
            return;
          }
        }

        if (mountedRef.current) {
          setStatusMessage('Rerender is still running. Refresh projects to check the latest status.');
        }
      } catch (err) {
        if (mountedRef.current) {
          setError(err.message || 'Failed to poll rerender job status.');
        }
      } finally {
        if (mountedRef.current) {
          setRerendering(false);
        }
      }
    },
    [onPagesUpdated, onProjectRefresh, project?.id],
  );

  const runPageAction = useCallback(
    async (action, actionPayload, selection = {}) => {
      if (!project?.id || actioning || saving || rerendering) return;
      const allowDirtyPageIndex =
        typeof selection.allowDirtyPageIndex === 'number' ? selection.allowDirtyPageIndex : null;
      const dirtyIndexes = Array.from(dirtyPageIndexes);
      const canUseSelectedDraft =
        allowDirtyPageIndex !== null &&
        draftPages.length === sourcePages.length &&
        dirtyIndexes.length > 0 &&
        dirtyIndexes.every((index) => index === allowDirtyPageIndex);

      if (isDirty && !canUseSelectedDraft) {
        setError('Save or discard unsaved transcript edits before using page structure controls.');
        setStatusMessage('');
        return;
      }

      setActioning(true);
      setError('');
      setStatusMessage(`${actionLabel(action)} in progress...`);
      setJobStatus(null);
      setPollingStartedAt(null);

      try {
        const response = await transcriptPageAction(project.id, {
          ...actionPayload,
          action,
          trigger_rerender: rerenderAfterAction,
          pause_sec: project?.tts_settings?.pause_seconds ?? undefined,
          lang_hint: 'auto',
        });
        const updatedPages = Array.isArray(response?.pages) ? response.pages : [];
        const nextDeletedPages = Array.isArray(response?.deleted_pages) ? response.deleted_pages : deletedPages;
        if (updatedPages.length) {
          onPagesUpdated?.(updatedPages);
        }
        setDeletedPages(nextDeletedPages);

        const selectPage = selection.selectPage || null;
        const selectIndex = typeof selection.selectIndex === 'number' ? selection.selectIndex : selectedDraftIndex;
        selectFromPages(updatedPages, selectPage, selectIndex);

        if (onProjectRefresh) {
          await onProjectRefresh();
        }

        const changedCount = Array.isArray(response?.changed_page_keys) ? response.changed_page_keys.length : 0;
        setStatusMessage(
          rerenderAfterAction
            ? `${actionLabel(action)} complete. Rerender queued for this project.`
            : `${actionLabel(action)} complete${changedCount ? ` (${changedCount} page${changedCount === 1 ? '' : 's'} affected)` : ''}.`,
        );

        const job = response?.rerender_job;
        const jobId = job?.id;
        if (rerenderAfterAction && jobId) {
          setJobStatus(job);
          await pollRerenderJob(jobId);
        } else if (rerenderAfterAction) {
          setStatusMessage(`${actionLabel(action)} complete. No rerender job was returned.`);
        }
      } catch (err) {
        setError(err.message || `${actionLabel(action)} failed.`);
        setStatusMessage('');
      } finally {
        if (mountedRef.current) {
          setActioning(false);
        }
      }
    },
    [
      actioning,
      deletedPages,
      dirtyPageIndexes,
      draftPages.length,
      isDirty,
      onPagesUpdated,
      onProjectRefresh,
      pollRerenderJob,
      project,
      rerenderAfterAction,
      rerendering,
      saving,
      selectFromPages,
      selectedDraftIndex,
      sourcePages.length,
    ],
  );

  const confirmPendingAction = useCallback(() => {
    const pending = pendingConfirmation;
    if (!pending) return;
    setPendingConfirmation(null);
    pending.onConfirm?.();
  }, [pendingConfirmation]);

  const splitSelectedPage = useCallback(() => {
    if (!selectedDraftPage?.id) return;
    const parts = splitByBlankLines(selectedDraftPage.narration_text);
    const displayParts = splitByBlankLines(displayValue(selectedDraftPage));
    const displayMatchesNarrationParts = displayParts.length === parts.length;
    if (parts.length < 2) {
      setError('Add a double blank line in the selected narration before splitting.');
      setStatusMessage('');
      return;
    }
    const otherDirtyIndexes = Array.from(dirtyPageIndexes).filter((index) => index !== selectedDraftIndex);
    if (otherDirtyIndexes.length > 0 || draftPages.length !== sourcePages.length) {
      setError('Save or discard edits on other pages before splitting the selected page.');
      setStatusMessage('');
      return;
    }
    setPendingConfirmation({
      title: 'Split selected narration',
      message: `Split creates ${parts.length} separate narration scenes using the same slide image/source. ${pageDescriptor(selectedDraftPage, selectedDraftIndex)} will keep the first part.`,
      confirmLabel: 'Confirm Split',
      onConfirm: () =>
        runPageAction(
          'split_page',
          {
            page_id: selectedDraftPage.id,
            parts: parts.map((part, index) => ({
              narration_text: part,
              original_text: displayMatchesNarrationParts ? displayParts[index] : part,
            })),
          },
          {
            selectPage: selectedDraftPage,
            selectIndex: selectedDraftIndex,
            allowDirtyPageIndex: selectedDraftIndex,
          },
        ),
    });
  }, [dirtyPageIndexes, draftPages.length, runPageAction, selectedDraftIndex, selectedDraftPage, sourcePages.length]);

  const mergeSelectedPage = useCallback(
    (direction) => {
      if (!selectedDraftPage?.id) return;
      const label = direction === 'previous' ? 'previous' : 'next';
      const affectedIndex = direction === 'previous' ? selectedDraftIndex - 1 : selectedDraftIndex + 1;
      const affectedPage = draftPages[affectedIndex];
      const survivor =
        direction === 'previous' && selectedDraftIndex > 0
          ? affectedPage
          : selectedDraftPage;
      setPendingConfirmation({
        title: `Merge with ${label}`,
        message: `${pageDescriptor(selectedDraftPage, selectedDraftIndex)} will be merged with ${pageDescriptor(affectedPage, affectedIndex)}. The merged-away page is soft-deleted and can be restored later.`,
        confirmLabel: 'Confirm Merge',
        onConfirm: () =>
          runPageAction(
            direction === 'previous' ? 'merge_with_previous' : 'merge_with_next',
            { page_id: selectedDraftPage.id },
            { selectPage: survivor, selectIndex: direction === 'previous' ? selectedDraftIndex - 1 : selectedDraftIndex },
          ),
      });
    },
    [draftPages, runPageAction, selectedDraftIndex, selectedDraftPage],
  );

  const moveSelectedPage = useCallback(
    (direction) => {
      if (!selectedDraftPage?.id) return;
      const targetIndex = direction === 'up' ? selectedDraftIndex - 1 : selectedDraftIndex + 1;
      if (targetIndex < 0 || targetIndex >= draftPages.length) return;
      const reordered = [...draftPages];
      const [moved] = reordered.splice(selectedDraftIndex, 1);
      reordered.splice(targetIndex, 0, moved);
      runPageAction(
        'reorder_pages',
        { page_ids: reordered.map((page) => page.id) },
        { selectPage: selectedDraftPage, selectIndex: targetIndex },
      );
    },
    [draftPages, runPageAction, selectedDraftIndex, selectedDraftPage],
  );

  const deleteSelectedPage = useCallback(() => {
    if (!selectedDraftPage?.id) return;
    const fallbackIndex = Math.min(selectedDraftIndex, Math.max(draftPages.length - 2, 0));
    setPendingConfirmation({
      title: 'Delete page',
      message: `${pageDescriptor(selectedDraftPage, selectedDraftIndex)} will be hidden from the active transcript but can be restored from Deleted Pages.`,
      confirmLabel: 'Confirm Delete',
      danger: true,
      onConfirm: () => runPageAction('delete_page', { page_id: selectedDraftPage.id }, { selectIndex: fallbackIndex }),
    });
  }, [draftPages.length, runPageAction, selectedDraftIndex, selectedDraftPage]);

  const restoreDeletedPage = useCallback(
    (page) => {
      if (!page?.id) return;
      setPendingConfirmation({
        title: 'Restore page',
        message: `${pageDescriptor(page, draftPages.length)} will be restored at the end of the active transcript.`,
        confirmLabel: 'Confirm Restore',
        onConfirm: () =>
          runPageAction(
            'restore_page',
            { page_id: page.id, position: 'end' },
            { selectPage: page, selectIndex: draftPages.length },
          ),
      });
    },
    [draftPages.length, runPageAction],
  );

  const controlsDisabled = saving || rerendering || actioning;

  const applyNarrationSuggestion = useCallback((suggestion = {}) => {
    if (controlsDisabled) {
      return { ok: false, message: 'Transcript editor is busy.' };
    }
    const nextText = suggestionDraftNarrationText(suggestion);
    if (!nextText) {
      setError('This suggestion does not include an AI draft narration to apply.');
      setStatusMessage('');
      return { ok: false, message: 'This suggestion does not include an AI draft narration to apply.' };
    }

    const requestedKey = textValue(suggestion.pageKey || suggestion.page_key).trim();
    const requestedPageNumber = Number(suggestion.pageNumber || suggestion.page_number || 0);
    let targetIndex = -1;
    if (requestedKey) {
      targetIndex = draftPages.findIndex((page, index) => pageKey(page, index) === requestedKey);
    }
    if (targetIndex < 0 && Number.isFinite(requestedPageNumber) && requestedPageNumber > 0) {
      const index = Math.floor(requestedPageNumber) - 1;
      if (index >= 0 && index < draftPages.length) targetIndex = index;
    }
    if (targetIndex < 0) {
      const message = 'Could not find the target transcript page for this suggestion.';
      setError(message);
      setStatusMessage('');
      return { ok: false, message };
    }

    const targetPage = draftPages[targetIndex];
    const currentNarrationRaw = textValue(targetPage?.narration_text);
    const currentNarration = currentNarrationRaw.trim();
    if (currentNarration && currentNarration !== nextText) {
      const confirmed = window.confirm(`Replace current narration for ${pageDescriptor(targetPage, targetIndex)} with this AI draft?`);
      if (!confirmed) {
        return { ok: false, cancelled: true, message: 'Suggestion was not applied.' };
      }
    }

    let updatedPage = null;
    setDraftPages((current) =>
      current.map((page, pageIndex) => {
        if (pageIndex !== targetIndex) return page;
        const flags = {
          ...editorTextFlags(page),
          narration_customized: true,
        };
        updatedPage = {
          ...page,
          narration_text: nextText,
          editor_document: {
            ...(page.editor_document || {}),
            text: flags,
          },
        };
        return updatedPage;
      }),
    );
    const targetKey = pageKey(targetPage, targetIndex);
    setAiAppliedDraftsByPageKey((current) => ({
      ...current,
      [targetKey]: {
        previousText: currentNarrationRaw,
        appliedText: nextText,
        appliedAt: Date.now(),
      },
    }));
    setError('');
    setStatusMessage('AI draft applied. Review it, then save changes when ready.');
    onSelectPage?.(updatedPage || targetPage, targetIndex);
    return { ok: true, pageIndex: targetIndex, pageKey: targetKey };
  }, [controlsDisabled, draftPages, onSelectPage]);

  const undoAiDraft = useCallback((index) => {
    const page = draftPages[index];
    const key = pageKey(page, index);
    const marker = aiAppliedDraftsByPageKey[key];
    if (!marker) return;
    const previousText = textValue(marker.previousText);
    setDraftPages((current) =>
      current.map((candidate, pageIndex) => {
        if (pageIndex !== index) return candidate;
        const flags = {
          ...editorTextFlags(candidate),
          narration_customized: true,
        };
        return {
          ...candidate,
          narration_text: previousText,
          editor_document: {
            ...(candidate.editor_document || {}),
            text: flags,
          },
        };
      }),
    );
    setAiAppliedDraftsByPageKey((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
    setError('');
    setStatusMessage('AI draft undone.');
    onSelectPage?.({ ...page, narration_text: previousText }, index);
  }, [aiAppliedDraftsByPageKey, draftPages, onSelectPage]);

  const saveTranscript = useCallback(
    async ({ triggerRerender = false } = {}) => {
      if (!project?.id || saving || rerendering) return;

      setSaving(true);
      setError('');
      setStatusMessage(triggerRerender ? 'Saving transcript and starting rerender...' : 'Saving transcript...');
      setJobStatus(null);
      setPollingStartedAt(null);

      try {
        const payloadPages = draftPages
          .filter((page, index) => dirtyPageIndexes.has(index))
          .map(buildPayloadPage);
        if (!payloadPages.length && !triggerRerender) {
          setStatusMessage('No transcript changes to save.');
          return;
        }

        const response = await updateProjectTranscript(project.id, payloadPages, {
          triggerRerender,
          pauseSec: project?.tts_settings?.pause_seconds ?? undefined,
          langHint: 'auto',
        });

        let updatedPages = null;
        try {
          updatedPages = await refetchTranscriptPages(project.id);
        } catch {
          updatedPages = Array.isArray(response?.pages) ? response.pages : null;
        }
        if (Array.isArray(updatedPages)) {
          onPagesUpdated?.(updatedPages);
        }
        onModerationUpdated?.(response);
        setAiAppliedDraftsByPageKey({});
        setStatusMessage(triggerRerender ? 'Transcript saved. Rerender queued.' : 'Transcript saved.');

        if (onProjectRefresh) {
          await onProjectRefresh();
        }

        const job = response?.rerender_job;
        const jobId = job?.id;
        if (triggerRerender && jobId) {
          setJobStatus(job);
          await pollRerenderJob(jobId);
        } else if (triggerRerender) {
          setStatusMessage('Transcript saved. No rerender job was returned.');
        }
        return response;
      } catch (err) {
        setError(err.message || 'Failed to save transcript edits.');
        setStatusMessage('');
      } finally {
        if (mountedRef.current) {
          setSaving(false);
        }
      }
    },
    [dirtyPageIndexes, draftPages, onModerationUpdated, onPagesUpdated, onProjectRefresh, pollRerenderJob, project, rerendering, saving],
  );

  useImperativeHandle(ref, () => ({
    save: saveTranscript,
    hasUnsavedChanges: () => isDirty,
    isBusy: () => controlsDisabled,
    applyNarrationSuggestion,
  }), [applyNarrationSuggestion, controlsDisabled, isDirty, saveTranscript]);

  if (!project) {
    return (
      <div className="space-y-2">
        <p className="title-lg text-[var(--text-primary)]">Transcript</p>
        <p className="text-sm text-[var(--text-secondary)]">Select a lesson to edit its transcript.</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="space-y-2">
        <p className="title-lg text-[var(--text-primary)]">Transcript</p>
        <p className="inline-flex items-center gap-2 text-sm text-[var(--text-secondary)]">
          <LoaderCircle size={14} className="animate-spin" />
          <span>Loading transcript pages...</span>
        </p>
      </div>
    );
  }

  if (!draftPages.length) {
    return (
      <div className="space-y-2">
        <p className="title-lg text-[var(--text-primary)]">Transcript</p>
        <p className="text-sm text-[var(--text-secondary)]">No transcript pages available yet.</p>
      </div>
    );
  }

  const actionControlsDisabled = controlsDisabled || isDirty;
  const selectedHasSplitParts = splitByBlankLines(selectedDraftPage?.narration_text).length >= 2;
  const otherDirtyIndexes = Array.from(dirtyPageIndexes).filter((index) => index !== selectedDraftIndex);
  const selectedOnlyDirty =
    isDirty &&
    draftPages.length === sourcePages.length &&
    dirtyPageIndexes.size > 0 &&
    otherDirtyIndexes.length === 0;
  const splitControlsDisabled =
    controlsDisabled ||
    !selectedDraftPage?.id ||
    !selectedHasSplitParts ||
    otherDirtyIndexes.length > 0 ||
    draftPages.length !== sourcePages.length;
  const selectedIsFirst = selectedDraftIndex <= 0;
  const selectedIsLast = selectedDraftIndex < 0 || selectedDraftIndex >= draftPages.length - 1;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="title-lg text-[var(--text-primary)]">Transcript</p>
          <p className="text-xs text-[var(--text-secondary)]">
            Edit slide display text and spoken narration separately.
          </p>
        </div>
        <span
          className={`rounded-full px-3 py-1 text-xs font-semibold ${
            isDirty
              ? 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]'
              : 'bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]'
          }`}
        >
          {isDirty ? `${dirtyPageIndexes.size} unsaved change${dirtyPageIndexes.size === 1 ? '' : 's'}` : 'Saved'}
        </span>
      </div>

      {showLocalActions ? (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => saveTranscript({ triggerRerender: false })} disabled={controlsDisabled || !isDirty}>
            {saving && !rerendering ? <LoaderCircle size={14} className="animate-spin" /> : <Save size={14} />}
            <span>Save</span>
          </Button>
          <Button size="sm" variant="secondary" onClick={() => saveTranscript({ triggerRerender: true })} disabled={controlsDisabled}>
            {rerendering ? <LoaderCircle size={14} className="animate-spin" /> : <RefreshCcw size={14} />}
            <span>Save + Rerender</span>
          </Button>
          <Button size="sm" variant="ghost" onClick={resetDraft} disabled={controlsDisabled || !isDirty}>
            <RotateCcw size={14} />
            <span>Discard Changes</span>
          </Button>
        </div>
      ) : isDirty ? (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="ghost" onClick={resetDraft} disabled={controlsDisabled}>
            <RotateCcw size={14} />
            <span>Discard Changes</span>
          </Button>
        </div>
      ) : null}

      <div className="space-y-3 rounded-2xl token-surface p-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="label-sm">Page Controls</p>
            <p className="mt-1 text-xs text-[var(--text-secondary)]">
              Split, merge, move, delete, and restore transcript pages through the backend. Split creates separate narration scenes using the same slide image/source. Drag-and-drop reorder is coming later.
            </p>
          </div>
          <label className="inline-flex items-center gap-2 rounded-xl px-2 py-1 text-xs text-[var(--text-secondary)]">
            <input
              type="checkbox"
              checked={rerenderAfterAction}
              onChange={(event) => setRerenderAfterAction(event.target.checked)}
              disabled={controlsDisabled}
            />
            <span>Rerender after page action</span>
          </label>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" onClick={splitSelectedPage} disabled={splitControlsDisabled}>
            {actioning ? <LoaderCircle size={14} className="animate-spin" /> : <Scissors size={14} />}
            <span>Split by blank lines</span>
          </Button>
          <Button size="sm" variant="secondary" onClick={() => mergeSelectedPage('previous')} disabled={actionControlsDisabled || !selectedDraftPage?.id || selectedIsFirst}>
            <Merge size={14} />
            <span>Merge with Previous</span>
          </Button>
          <Button size="sm" variant="secondary" onClick={() => mergeSelectedPage('next')} disabled={actionControlsDisabled || !selectedDraftPage?.id || selectedIsLast}>
            <Merge size={14} />
            <span>Merge with Next</span>
          </Button>
          <Button size="sm" variant="ghost" onClick={() => moveSelectedPage('up')} disabled={actionControlsDisabled || !selectedDraftPage?.id || selectedIsFirst}>
            <ArrowUp size={14} />
            <span>Move Up</span>
          </Button>
          <Button size="sm" variant="ghost" onClick={() => moveSelectedPage('down')} disabled={actionControlsDisabled || !selectedDraftPage?.id || selectedIsLast}>
            <ArrowDown size={14} />
            <span>Move Down</span>
          </Button>
          <Button size="sm" variant="ghost" onClick={deleteSelectedPage} disabled={actionControlsDisabled || !selectedDraftPage?.id || draftPages.length <= 1}>
            <Trash2 size={14} />
            <span>Delete Page</span>
          </Button>
        </div>

        {isDirty && (
          <p className="text-xs text-[var(--text-secondary)]">
            {selectedOnlyDirty && selectedHasSplitParts
              ? 'Split can use the current selected draft. Save or discard before merge, move, delete, or restore.'
              : 'Save or discard unsaved transcript edits before using page structure controls.'}
          </p>
        )}

        {pendingConfirmation && (
          <div
            role="dialog"
            aria-modal="false"
            className="space-y-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-container-high)] p-3"
          >
            <div>
              <p className="font-semibold text-[var(--text-primary)]">{pendingConfirmation.title}</p>
              <p className="mt-1 text-sm text-[var(--text-secondary)]">{pendingConfirmation.message}</p>
            </div>
            <div className="flex flex-wrap justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => setPendingConfirmation(null)} disabled={actioning}>
                <span>Cancel</span>
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={confirmPendingAction}
                disabled={actioning}
              >
                <span>{pendingConfirmation.confirmLabel || 'Confirm'}</span>
              </Button>
            </div>
          </div>
        )}

        {deletedPages.length > 0 && (
          <div className="space-y-2 rounded-xl bg-[var(--surface-container-high)] p-3">
            <p className="label-sm">Deleted Pages</p>
            {deletedPages.map((page, index) => (
              <div key={page.page_key || page.id || index} className="flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--text-secondary)]">
                <span className="line-clamp-1">
                  {page.page_key || `Deleted page ${index + 1}`} - {textValue(page.narration_text || page.original_text).replace(/\s+/g, ' ').trim() || 'No text'}
                </span>
                <Button size="sm" variant="secondary" onClick={() => restoreDeletedPage(page)} disabled={actionControlsDisabled}>
                  <Undo2 size={14} />
                  <span>Restore</span>
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>

      {(statusMessage || error || jobStatus) && (
        <div className="space-y-2 rounded-2xl token-surface p-3 text-sm">
          {statusMessage && (
            <p className="inline-flex items-center gap-2 text-[var(--text-secondary)]">
              <CheckCircle2 size={14} />
              <span>{statusMessage}</span>
            </p>
          )}
          {error && (
            <p className="inline-flex items-center gap-2 text-[color:var(--status-danger-fg)]">
              <AlertTriangle size={14} />
              <span>{error}</span>
            </p>
          )}
          {jobStatus && (
            <p className="text-xs text-[var(--text-secondary)]">
              Rerender job: {jobStatusLabel(jobStatus)}
              {pollingStartedAt ? ` - polling since ${new Date(pollingStartedAt).toLocaleTimeString()}` : ''}
            </p>
          )}
        </div>
      )}

      <div className="space-y-3">
        {draftPages.map((page, index) => {
          const label = page.source_slide_index !== undefined && page.source_slide_index !== null
            ? `Slide ${Number(page.source_slide_index) + 1}`
            : `Slide ${index + 1}`;
          const narration = textValue(page.narration_text);
          const displayText = displayValue(page);
          const diffLabel = pageDiffLabel(page, index);
          const key = pageKey(page, index);
          const selected = key === activePageKey;
          const editingDisplay = Boolean(displayEditKeys[key]);
          const moderationWarning = moderationPageWarnings[key] || null;
          const moderationFields = new Set(moderationWarning?.fields || []);
          const hasModerationWarning = Boolean(moderationWarning);
          const displayWarned = moderationFields.has('original_text') || moderationFields.has('page');
          const narrationWarned = moderationFields.has('narration_text') || moderationFields.has('page');
          const aiDraftMarker = aiAppliedDraftsByPageKey[key] || null;

          return (
            <article
              key={key}
              ref={(node) => {
                if (node) {
                  pageRefs.current[key] = node;
                } else {
                  delete pageRefs.current[key];
                }
              }}
              onClick={() => onSelectPage?.(page, index)}
              className={`space-y-3 rounded-2xl p-3 transition ${
                selected
                  ? `border ${hasModerationWarning ? 'border-[color:var(--status-warning-fg)]' : 'border-[color:rgba(208,188,255,0.55)]'} bg-[color:rgba(208,188,255,0.12)]`
                  : hasModerationWarning
                    ? 'border border-[color:var(--status-warning-fg)] bg-[color:var(--status-warning-bg)]'
                    : 'token-surface'
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="font-medium text-[var(--text-primary)]">{label}</p>
                  <p className="text-xs text-[var(--text-secondary)]">
                    {page.page_key ? `Page key: ${page.page_key}` : `Page ${index + 1}`}
                  </p>
                </div>
                <span
                  className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                    diffLabel === 'edited' || diffLabel === 'split candidate'
                      ? 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]'
                      : diffLabel === 'empty'
                        ? 'bg-[color:var(--status-danger-bg)] text-[color:var(--status-danger-fg)]'
                        : 'bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]'
                  }`}
                >
                  {diffLabel}
                </span>
              </div>

              {hasModerationWarning && (
                <p className="inline-flex items-center gap-2 rounded-full bg-[color:var(--status-warning-bg)] px-3 py-1 text-xs font-semibold text-[color:var(--status-warning-fg)]">
                  <AlertTriangle size={12} />
                  <span>Moderation finding on this slide</span>
                </p>
              )}

              <div className={`rounded-xl p-3 ${
                displayWarned
                  ? 'border border-[color:var(--status-warning-fg)] bg-[color:var(--status-warning-bg)]'
                  : 'bg-[var(--surface-container-high)]'
              }`}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="label-sm">Original / display text</p>
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">Visible on this scene.</p>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={(event) => {
                      event.stopPropagation();
                      toggleDisplayEdit(key);
                    }}
                    disabled={controlsDisabled}
                  >
                    <Pencil size={14} />
                    <span>{editingDisplay ? 'Done' : 'Edit'}</span>
                  </Button>
                </div>
                {editingDisplay ? (
                  <textarea
                    value={displayText}
                    onFocus={() => onSelectPage?.(page, index)}
                    onChange={(event) => updateDisplayText(index, event.target.value)}
                    className={`focus-ring mt-2 min-h-[130px] w-full resize-y rounded-xl border bg-[var(--surface-elevated)] p-3 text-sm leading-6 text-[var(--text-primary)] ${
                      displayWarned ? 'border-[color:var(--status-warning-fg)]' : 'border-[var(--border-subtle)]'
                    }`}
                    placeholder="Text visible on the slide..."
                  />
                ) : (
                  <p className="mt-2 whitespace-pre-wrap text-sm text-[var(--text-secondary)]">
                    {displayText || 'No display text yet.'}
                  </p>
                )}
              </div>

              <div className={`block rounded-xl text-sm ${
                narrationWarned
                  ? 'border border-[color:var(--status-warning-fg)] bg-[color:var(--status-warning-bg)] p-3 text-[color:var(--status-warning-fg)]'
                  : aiDraftMarker
                    ? 'border border-[color:rgba(208,188,255,0.55)] bg-[color:rgba(208,188,255,0.11)] p-3 text-[var(--text-secondary)]'
                    : 'text-[var(--text-secondary)]'
              }`}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span>Narration text / captions</span>
                  {aiDraftMarker && (
                    <span className="inline-flex flex-wrap items-center gap-2">
                      <span className="inline-flex items-center gap-1 rounded-full bg-[color:rgba(208,188,255,0.18)] px-2.5 py-1 text-xs font-semibold text-[var(--accent-primary)]">
                        <Sparkles size={12} />
                        <span>AI draft</span>
                      </span>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={(event) => {
                          event.preventDefault();
                          event.stopPropagation();
                          undoAiDraft(index);
                        }}
                        disabled={controlsDisabled}
                      >
                        <Undo2 size={14} />
                        <span>Undo</span>
                      </Button>
                    </span>
                  )}
                </div>
                <textarea
                  aria-label="Narration text / captions"
                  value={narration}
                  onFocus={() => onSelectPage?.(page, index)}
                  onChange={(event) => updateNarrationText(index, event.target.value)}
                  className={`focus-ring mt-1 w-full resize-y rounded-2xl border bg-[var(--surface-elevated)] p-4 text-[var(--text-primary)] ${
                    narrationWarned
                      ? 'border-[color:var(--status-warning-fg)]'
                      : aiDraftMarker
                        ? 'border-[color:rgba(208,188,255,0.7)] shadow-[0_0_0_1px_rgba(208,188,255,0.16)]'
                        : 'border-[var(--border-subtle)]'
                  } ${
                    focusMode ? 'min-h-[280px] text-base leading-7' : 'min-h-[190px] text-[0.95rem] leading-7'
                  }`}
                  placeholder="Edit spoken narration for this slide..."
                />
              </div>

              {!narration.trim() && (
                <p className="inline-flex items-center gap-2 text-xs text-[color:var(--status-danger-fg)]">
                  <AlertTriangle size={12} />
                  <span>Narration is blank. Saving is allowed, but this slide may render without spoken text.</span>
                </p>
              )}

              {hasDoubleBlankLine(narration) && (
                <p className="text-xs text-[var(--text-secondary)]">
                  Double blank lines are ready for the Split by blank lines control. Normal Save keeps this as one page.
                </p>
              )}

              <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--text-secondary)]">
                <label className="inline-flex items-center gap-2 rounded-xl px-2 py-1">
                  <input
                    type="checkbox"
                    checked={Boolean(page.whiteboard_mode)}
                    onFocus={() => onSelectPage?.(page, index)}
                    onChange={(event) => updateDraftPage(index, { whiteboard_mode: event.target.checked })}
                  />
                  <span>Whiteboard mode</span>
                </label>
                <span>{page.subtitle_chunks.length} subtitle chunk{page.subtitle_chunks.length === 1 ? '' : 's'}</span>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
});

export default TranscriptEditorPanel;
