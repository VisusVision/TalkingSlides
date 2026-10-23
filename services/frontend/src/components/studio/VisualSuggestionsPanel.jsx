import { useEffect, useId, useRef, useState } from 'react';
import { fetchVisualSuggestions, generateVisualSuggestion } from '../../api';
import { useStudioWorkspaceCopy } from './StudioWorkspaceChrome';
import Button from '../ui/Button';
import Dialog from '../ui/Dialog';
import { usePrivatePreview, usePrivateOriginal } from './visualPreviewHooks';
import VisualDraftControls from './VisualDraftControls';

export function StudioIntelligenceTabs({ analysis, children }) {
  const copy = useStudioWorkspaceCopy();
  const id = useId();
  const [active, setActive] = useState('analysis');
  return <div>
    <div role="tablist" aria-label={copy.inspectorIntelligencePanel} className="mb-3 flex flex-wrap gap-2" onKeyDown={(event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 'analysis' : event.key === 'End' ? 'visuals' : active === 'analysis' ? 'visuals' : 'analysis';
      setActive(next); event.currentTarget.querySelector(`[data-view="${next}"]`)?.focus();
    }}>
      {['analysis', 'visuals'].map((view) => <Button key={view} role="tab" data-view={view} id={`${id}-${view}-tab`} aria-controls={`${id}-${view}`} aria-selected={active === view} tabIndex={active === view ? 0 : -1} variant={active === view ? 'primary' : 'secondary'} size="sm" onClick={() => setActive(view)}>{view === 'analysis' ? copy.visualAnalysis : copy.visualTab}</Button>)}
    </div>
    <div role="tabpanel" id={`${id}-analysis`} aria-labelledby={`${id}-analysis-tab`} hidden={active !== 'analysis'}>{analysis}</div>
    {active === 'visuals' && <div role="tabpanel" id={`${id}-visuals`} aria-labelledby={`${id}-visuals-tab`}>{children}</div>}
  </div>;
}

export default function VisualSuggestionsPanel({ projectId, page, originalImageUrl = '', unsavedChanges = false, readOnly = false, revision = '' }) {
  const copy = useStudioWorkspaceCopy();
  const pageKey = page?.page_key || '';
  const [data, setData] = useState(null);
  const [selectedId, setSelectedId] = useState('');
  const [expanded, setExpanded] = useState(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [reused, setReused] = useState(false);
  const generationBusy = useRef(false);
  const requestEpoch = useRef(0);
  const candidate = data?.suggestions?.find((item) => item.id === selectedId) || data?.suggestions?.[0] || null;
  const privatePreview = usePrivatePreview(projectId, candidate, refreshVersion);
  const savedOriginalImage = usePrivateOriginal(projectId, data?.source);
  const originalImage = savedOriginalImage || originalImageUrl;
  const sourceToken = JSON.stringify([revision, page?.original_text, page?.narration_text, page?.editor_document, page?.source_slide_index, page?.split_index]);

  useEffect(() => {
    const epoch = ++requestEpoch.current;
    setData(null); setSelectedId(''); setExpanded(null); setError(false); setBusy(false); setReused(false); generationBusy.current = false;
    if (!projectId || !pageKey || readOnly) { setLoading(false); return undefined; }
    setLoading(true);
    fetchVisualSuggestions(projectId, pageKey).then((next) => {
      if (epoch !== requestEpoch.current) return;
      setData(next); setLoading(false);
    }).catch(() => { if (epoch === requestEpoch.current) { setError(true); setLoading(false); } });
    return () => { requestEpoch.current += 1; };
  }, [projectId, pageKey, sourceToken, readOnly, refreshVersion]);

  async function generate() {
    if (generationBusy.current || busy || unsavedChanges || readOnly || !data?.source) return;
    generationBusy.current = true;
    const epoch = requestEpoch.current;
    setBusy(true); setError(false);
    try {
      const result = await generateVisualSuggestion(projectId, pageKey, data.source.source_hash);
      const next = await fetchVisualSuggestions(projectId, pageKey);
      if (epoch !== requestEpoch.current) return;
      setData(next); setSelectedId(result.suggestion.id); setReused(Boolean(result.reused));
    } catch { if (epoch === requestEpoch.current) setError(true); }
    finally { if (epoch === requestEpoch.current) { setBusy(false); generationBusy.current = false; } }
  }
  const strategyLabel = { preserve_original: copy.visualStrategyPreserve, typography_only: copy.visualStrategyTypography, flowchart: copy.visualStrategyFlowchart, comparison: copy.visualStrategyComparison, chart: copy.visualStrategyChart }[candidate?.strategy];

  if (readOnly) return <p className="token-surface rounded-xl p-4">{copy.visualReadOnly}</p>;
  if (!projectId || !pageKey) return <p className="token-surface rounded-xl p-4">{copy.visualSelectPage}</p>;
  return <section aria-label={copy.visualTitle} className="min-w-0 rounded-2xl token-surface p-4">
    <Dialog open={Boolean(expanded)} onClose={() => setExpanded(null)} size="full">
      <Dialog.Header><Dialog.Title>{expanded?.title}</Dialog.Title><Dialog.Close closeLabel={copy.visualClose} onClose={() => setExpanded(null)} /></Dialog.Header>
      <Dialog.Body data-testid="visual-expanded-content">{expanded && <img src={expanded.url} alt={expanded.title} className="max-h-[80vh] w-full object-contain" />}</Dialog.Body>
    </Dialog>
    <h3 className="title-md text-[var(--text-primary)]">{copy.visualTitle}</h3>
    <p className="mt-2 text-sm text-[var(--text-secondary)]">{copy.visualHint}</p>
    {unsavedChanges && <p role="status" className="mt-3 text-sm text-[var(--status-warning-fg)]">{copy.visualUnsaved}</p>}
    <div className="my-3 flex flex-wrap gap-2">
      <Button size="sm" disabled={busy || loading || unsavedChanges || !data?.source} onClick={generate}>{busy ? copy.visualGenerating : candidate ? copy.visualRegenerate : copy.visualGenerate}</Button>
      <Button size="sm" variant="secondary" disabled={busy || loading} onClick={() => setRefreshVersion((n) => n + 1)}>{copy.visualRefresh}</Button>
    </div>
    {(loading || busy) && <p role="status">{busy ? copy.visualGenerating : copy.visualLoading}</p>}
    {reused && <p role="status" className="text-sm">{copy.visualReused}</p>}
    {error && <p role="alert" className="text-sm text-[var(--status-danger-fg)]">{copy.visualError}</p>}
    {!loading && data && !candidate && <p className="text-sm">{data.source ? copy.visualEmpty : copy.visualSelectPage}</p>}
    {data?.source && <p className="my-3 text-xs text-[var(--text-secondary)]">{data.source.source_mode === 'draft' ? copy.visualDraft : copy.visualActive}</p>}
    {data?.suggestions?.length > 1 && <label className="my-3 block text-sm">{copy.visualHistory}<select aria-label={copy.visualHistory} value={candidate?.id || ''} className="mt-1 block w-full rounded-lg token-surface p-2" onChange={(event) => setSelectedId(event.target.value)}>{data.suggestions.map((item, i) => <option key={item.id} value={item.id}>{i + 1} · {item.created_at} · {item.is_stale ? copy.visualStale : copy.visualCurrent}</option>)}</select></label>}
    <div className="grid min-w-0 grid-cols-1 gap-3 2xl:grid-cols-2">
      <figure className="min-w-0 rounded-xl border border-[var(--border-subtle)] p-3"><figcaption className="mb-2 text-sm font-semibold">{copy.visualOriginal}</figcaption>
        {originalImage ? <img src={originalImage} alt={copy.visualOriginal} className="aspect-video w-full object-contain" /> : <><p className="text-xs text-[var(--text-secondary)]">{copy.visualNoOriginal}</p><p dir="auto" className="mt-2 whitespace-pre-wrap break-words text-sm">{page?.original_text}</p></>}
        {originalImage && <Button size="sm" variant="secondary" className="mt-2" onClick={() => setExpanded({ url: originalImage, title: copy.visualOriginal })}>{copy.visualExpand}</Button>}
      </figure>
      {candidate && <figure className="min-w-0 rounded-xl border border-[var(--border-subtle)] p-3"><figcaption className="mb-2 text-sm font-semibold">{copy.visualSuggestion}</figcaption>
        {privatePreview.loading && <p role="status">{copy.visualLoading}</p>}{privatePreview.failed && <p role="alert">{copy.visualError}</p>}
        {privatePreview.image && <img src={privatePreview.image} alt={copy.visualSuggestion} className="aspect-video w-full object-contain" />}
        {candidate.compatible === false && <p className="text-sm">{copy.visualUnsupported}</p>}
        {(privatePreview.image || candidate.status === 'preserved' && originalImage) && <Button size="sm" variant="secondary" className="my-2" onClick={() => setExpanded({ url: privatePreview.image || originalImage, title: copy.visualSuggestion })}>{copy.visualExpand}</Button>}
        {candidate.status === 'preserved' && <>{originalImage && <img src={originalImage} alt={copy.visualSuggestion} className="aspect-video w-full object-contain" />}<p className="text-sm">{copy.visualPreserved}</p></>}{candidate.status === 'unsupported' && <p className="text-sm">{copy.visualUnsupported}</p>}{candidate.status === 'failed' && <p role="alert">{copy.visualFailed}</p>}
      </figure>}
    </div>
    {data?.source && <VisualDraftControls key={`${projectId}:${pageKey}:${data.source.source_hash}`} projectId={projectId} pageKey={pageKey} source={data.source} candidate={candidate} originalImage={originalImage} originalText={page?.original_text} unsavedChanges={unsavedChanges} refreshVersion={refreshVersion} onRefreshSource={() => setRefreshVersion((n) => n + 1)} />}
    {candidate && <div className="mt-4 space-y-2 text-sm">
      <p className="font-semibold">{strategyLabel || copy.visualUnsupported}</p>
      <p role="status">{candidate.is_stale ? copy.visualStale : copy.visualCurrent}{unsavedChanges ? ` · ${copy.visualWorkingChanged}` : ''}</p>
      <p><strong>{copy.visualRationale}: </strong>{candidate.rationale || copy.visualUnsupported}</p>
      {candidate.fallback_reason && <p className="text-[var(--text-secondary)]">{candidate.fallback_reason}</p>}
      {(candidate.warnings?.length > 0 || candidate.errors?.length > 0) && <div><strong>{copy.visualWarnings}</strong><ul className="mt-1 list-disc space-y-1 ps-5">{[...(candidate.warnings || []), ...(candidate.errors || [])].map((note, index) => <li key={index}>{note}</li>)}</ul></div>}
      <p className="font-semibold">{copy.visualPreviewOnly}</p>
    </div>}
  </section>;
}
