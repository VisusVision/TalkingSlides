import { useEffect, useRef, useState } from 'react';
import { fetchVisualDraft, saveVisualDecision } from '../../api';
import { useStudioWorkspaceCopy } from './StudioWorkspaceChrome';
import { usePrivatePreview } from './visualPreviewHooks';
import Button from '../ui/Button';

const original = { state: 'original', candidate_id: null };
const sameChoice = (a, b) => a?.state === b?.state && a?.candidate_id === b?.candidate_id;

export default function VisualDraftControls({ projectId, pageKey, source, candidate, originalImage, originalText, unsavedChanges, refreshVersion, onRefreshSource }) {
  const copy = useStudioWorkspaceCopy();
  const [saved, setSaved] = useState(null);
  const [working, setWorking] = useState(null);
  const [workingCandidate, setWorkingCandidate] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [pendingRestore, setPendingRestore] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const epoch = useRef(0), requestBusy = useRef(false);
  const preview = usePrivatePreview(projectId, workingCandidate, refreshVersion);

  function accept(data) {
    if (data?.schema_version !== 'visual-draft.v1' || data.page_key !== pageKey || !data.selection || !Number.isSafeInteger(data.selection.version) || data.source?.source_hash !== source.source_hash) throw new Error('Incompatible private visual draft');
    setSaved(data); setWorking(data.selection); setPendingRestore(false);
    setWorkingCandidate(data.selection.state === 'selected' && !data.selection.is_stale ? data.selected_candidate : null);
  }
  useEffect(() => {
    const ticket = ++epoch.current;
    setSaved(null); setWorking(null); setWorkingCandidate(null); setError(''); setBusy(false); requestBusy.current = false;
    fetchVisualDraft(projectId, pageKey).then((data) => { if (ticket === epoch.current) accept(data); })
      .catch(() => { if (ticket === epoch.current) setError(copy.visualDraftError); });
    return () => { epoch.current += 1; };
  }, [projectId, pageKey, source.source_hash, refreshVersion, refresh]);

  const invalid = !saved || busy || unsavedChanges || !source;
  const selectable = (item) => item?.status === 'ready' && item.compatible === true && !item.is_stale
    && item.source_hash === source.source_hash && !(item.errors?.length);
  const dirty = Boolean(working && saved && (!sameChoice(working, saved.selection) || pendingRestore));
  const rejected = saved?.rejected_ids?.includes(candidate?.id);
  const stagedStale = working?.state === 'selected' && (!selectable(workingCandidate) || working.is_stale);

  async function decide(action, candidateId) {
    if (invalid || requestBusy.current) return;
    const ticket = epoch.current;
    requestBusy.current = true; setBusy(true); setError('');
    try {
      const next = await saveVisualDecision(projectId, { pageKey, action, candidateId,
        expectedSourceHash: source.source_hash, expectedVersion: saved.selection.version });
      if (ticket === epoch.current) accept(next);
    } catch (failure) {
      if (ticket === epoch.current) setError(failure.status === 409 ? copy.visualDraftConflict : copy.visualDraftError);
    } finally { if (ticket === epoch.current) { setBusy(false); requestBusy.current = false; } }
  }
  function useDesign() {
    if (invalid || !selectable(candidate)) return;
    setWorking({ state: 'selected', candidate_id: candidate.id, is_stale: false });
    setWorkingCandidate(candidate); setPendingRestore(false); setError('');
  }
  function restore() {
    if (invalid) return;
    setWorking(original); setWorkingCandidate(null); setPendingRestore(true); setError('');
  }
  const showingGenerated = working?.state === 'selected' && !stagedStale;
  return <section aria-label={copy.visualPrivateDraft} className="mt-4 min-w-0 rounded-xl border border-[var(--border-subtle)] p-3">
    <h4 className="font-semibold">{copy.visualPrivateDraft}</h4>
    <p className="mt-1 text-xs text-[var(--text-secondary)]">{copy.visualDraftHint}</p>
    {!saved && !error && <p role="status">{copy.visualDraftLoading}</p>}
    {error && <div role="alert"><p>{error}</p><Button size="sm" variant="secondary" disabled={busy} onClick={() => onRefreshSource ? onRefreshSource() : setRefresh((v) => v + 1)}>{copy.visualReloadDraft}</Button></div>}
    {saved && <>
      <p role="status" className="my-2 text-sm font-semibold">{busy ? copy.visualDraftSaving : dirty ? copy.visualChoiceUnsaved : saved.selection.version > 0 ? copy.visualChoiceSaved : copy.visualChoiceOriginal}</p>
      {saved.selection.is_stale && <p role="status" className="text-sm text-[var(--status-warning-fg)]">{copy.visualStale}</p>}
      {rejected && <p role="status" className="text-sm">{copy.visualRejected}</p>}
      <div className="my-3 flex flex-wrap gap-2">
        <Button size="sm" aria-pressed={Boolean(working?.state === 'selected' && working.candidate_id === candidate?.id)} disabled={invalid || !selectable(candidate)} onClick={useDesign}>{copy.visualUseDesign}</Button>
        <Button size="sm" variant="secondary" disabled={invalid || !candidate} onClick={() => decide('reject', candidate.id)}>{copy.visualReject}</Button>
        <Button size="sm" variant="secondary" disabled={invalid} onClick={restore}>{copy.visualRestore}</Button>
        <Button size="sm" disabled={invalid || !dirty || stagedStale} onClick={() => decide(working.state === 'selected' ? 'select' : 'restore', working.candidate_id)}>{copy.visualSaveDraft}</Button>
      </div>
      {working?.state === 'selected' && candidate?.id === working.candidate_id && <p className="text-xs">{copy.visualSelected}</p>}
      <figure className="min-w-0 rounded-lg token-surface p-2" data-testid="visual-working-preview">
        <figcaption className="mb-2 text-sm">{copy.visualWorkingPreview} · {showingGenerated ? copy.visualSelectedDesign : copy.visualChoiceOriginal}</figcaption>
        {showingGenerated ? <>
          {preview.loading && <p role="status">{copy.visualLoading}</p>}
          {preview.failed && <p role="alert">{copy.visualDraftError}</p>}
          {preview.image && <img src={preview.image} alt={copy.visualSelectedDesign} className="aspect-video w-full object-contain" />}
        </> : originalImage ? <img src={originalImage} alt={copy.visualOriginal} className="aspect-video w-full object-contain" /> : <p dir="auto" className="whitespace-pre-wrap break-words text-sm">{originalText}</p>}
      </figure>
      {saved.history?.length > 0 && <details className="mt-3 text-xs"><summary className="cursor-pointer">{copy.visualDecisionHistory}</summary><ol className="mt-2 space-y-1">{saved.history.map((decision) => <li key={decision.version}>{({ select: copy.visualSelectedDesign, reject: copy.visualRejected, restore: copy.visualChoiceOriginal })[decision.action]} · {decision.version} · {decision.created_at}</li>)}</ol></details>}
    </>}
  </section>;
}
