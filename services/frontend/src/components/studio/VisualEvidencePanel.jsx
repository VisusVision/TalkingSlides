import { useEffect, useRef, useState } from 'react';
import { fetchVisualProposals, generateVisualProposal, reviewVisualProposal, generateVisualSuggestion } from '../../api';
import { useStudioWorkspaceCopy } from './StudioWorkspaceChrome';
import Button from '../ui/Button';

function Structure({ evidence }) {
  const data = evidence?.data;
  if (!data) return null;
  if (evidence.kind === 'process') return <ol className="list-decimal space-y-1 ps-5">{data.order.map((id) => <li key={id} dir="auto">{data.steps.find((step) => step.id === id)?.label}</li>)}</ol>;
  if (evidence.kind === 'comparison') return <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr><th /><th>{data.items[0].label}</th><th>{data.items[1].label}</th></tr></thead><tbody>{data.categories.map((category, i) => <tr key={category}><th>{category}</th>{data.items.map((item) => <td key={item.label}>{item.values[i]}</td>)}</tr>)}</tbody></table></div>;
  if (evidence.kind === 'numeric') return <div><p>{data.measure} ({data.unit})</p><ul>{data.points.map((point) => <li key={point.label}>{point.label}: {point.value}</li>)}</ul></div>;
  return null;
}

// Parent keys this by project/page/source hash. A late response cannot mutate a
// different page. All content is React text, never injected model/SVG markup.
export default function VisualEvidencePanel({ projectId, pageKey, source, candidate, disabled, onCandidate }) {
  const copy = useStudioWorkspaceCopy();
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState(false);
  const alive = useRef(true), locked = useRef(false);
  useEffect(() => {
    alive.current = true;
    fetchVisualProposals(projectId, pageKey).then((next) => { if (alive.current) setData(next); }).catch(() => { if (alive.current) setError(true); });
    return () => { alive.current = false; };
  }, [projectId, pageKey]);
  async function run(action, proposal) {
    if (locked.current || disabled || !source?.source_hash) return;
    locked.current = true; setBusy(true); setError(false); setMessage('');
    try {
      const result = action === 'generate'
        ? await generateVisualProposal(projectId, pageKey, source.source_hash)
        : await reviewVisualProposal(projectId, proposal.id, action, source.source_hash);
      if (!alive.current) return;
      if (result.proposal) {
        setData((old) => ({ ...old, proposals: [result.proposal, ...(old?.proposals || []).filter((item) => item.id !== result.proposal.id)] }));
      }
      if (result.suggestion) await onCandidate(result);
      if (result.fallback) {
        setMessage(copy.visualModelFallback);
        const fallback = await generateVisualSuggestion(projectId, pageKey, source.source_hash);
        if (alive.current) await onCandidate(fallback);
      }
    } catch { if (alive.current) setError(true); }
    finally { if (alive.current) { setBusy(false); locked.current = false; } }
  }
  const supports = candidate?.evidence?.supports || [];
  return <section aria-label={copy.visualEvidenceTitle} className="my-4 min-w-0 space-y-3 rounded-xl border border-[var(--border-subtle)] p-3 text-sm">
    <h4 className="font-semibold">{copy.visualEvidenceTitle}</h4>
    <p>{copy.visualEvidenceHint}</p>
    {supports.length > 0 && <details><summary className="cursor-pointer">{copy.visualSourceEvidence} · {candidate.evidence.origin === 'teacher_confirmed' ? copy.visualTeacherConfirmed : copy.visualSourceBacked}</summary>
      {supports.map((ref, i) => <blockquote key={i} dir="auto" className="my-2 whitespace-pre-wrap break-words border-s-2 ps-3"><p>{ref.quote}</p><cite className="text-xs">{ref.page_key} · {ref.locator}</cite></blockquote>)}
    </details>}
    {(candidate?.evidence?.warnings || []).map((warning, i) => <p key={i} role="status">{warning}</p>)}
    {data?.model_available && <Button size="sm" variant="secondary" disabled={disabled || busy} onClick={() => run('generate')}>{busy ? copy.visualGenerating : copy.visualModelPropose}</Button>}
    {!data?.model_available && data && <p className="text-xs text-[var(--text-secondary)]">{copy.visualModelUnavailable}</p>}
    {message && <p role="status">{message}</p>}{error && <p role="alert">{copy.visualEvidenceError}</p>}
    {(data?.proposals || []).map((proposal) => <article key={proposal.id} aria-label={copy.visualEvidenceProposal} className="space-y-2 rounded-lg token-surface p-3">
      <p className="font-semibold">{copy.visualEvidenceProposal}</p>
      <p>{copy.visualUnverified}</p><p>{proposal.rationale}</p>
      <Structure evidence={proposal.structure} />
      {(proposal.supports || []).map((ref, i) => <blockquote key={i} dir="auto" className="whitespace-pre-wrap break-words border-s-2 ps-3">{ref.quote}</blockquote>)}
      {(proposal.warnings || []).map((warning, i) => <p key={i}>{warning}</p>)}
      <p role="status">{proposal.is_stale ? copy.visualStale : proposal.decision === 'confirm' ? copy.visualTeacherConfirmed : proposal.decision === 'reject' ? copy.visualEvidenceRejected : copy.visualNeedsConfirmation}</p>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={disabled || busy || proposal.is_stale || proposal.decision !== 'pending' || proposal.source_hash !== source?.source_hash} onClick={() => run('confirm', proposal)}>{copy.visualConfirmEvidence}</Button>
        <Button size="sm" variant="secondary" disabled={disabled || busy || proposal.is_stale || proposal.decision !== 'pending'} onClick={() => run('reject', proposal)}>{copy.visualRejectEvidence}</Button>
      </div>
    </article>)}
  </section>;
}
