import { useEffect, useState } from 'react';
import { fetchVisualSuggestionPreview, fetchVisualOriginalPreview } from '../../api';

export function usePrivatePreview(projectId, candidate, refreshVersion) {
  const key = JSON.stringify([projectId, candidate?.id, candidate?.preview_url, candidate?.status, candidate?.compatible, candidate?.preview_hash, refreshVersion]);
  const [state, setState] = useState({});
  useEffect(() => {
    let cancelled = false, objectUrl = '';
    if (!candidate?.preview_url || candidate.status !== 'ready' || candidate.compatible === false) {
      setState({ key, image: '', failed: false, loading: false }); return undefined;
    }
    setState({ key, image: '', failed: false, loading: true });
    fetchVisualSuggestionPreview(projectId, candidate.id).then((url) => {
      objectUrl = url;
      if (cancelled) { URL.revokeObjectURL(url); return; }
      setState({ key, image: url, failed: false, loading: false });
    }).catch(() => { if (!cancelled) setState({ key, image: '', failed: true, loading: false }); });
    return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [key]);
  return state.key === key ? state : { image: '', failed: false, loading: false };
}

export function usePrivateOriginal(projectId, source) {
  const key = JSON.stringify([projectId, source?.source_hash, source?.asset_page_ref, source?.original_available, source?.source_mode]);
  const [state, setState] = useState({});
  useEffect(() => {
    let cancelled = false, objectUrl = '';
    setState({ key, image: '' });
    if (!source?.original_available || !source?.asset_page_ref) return undefined;
    fetchVisualOriginalPreview(projectId, source.asset_page_ref, source.source_mode === 'draft').then((url) => {
      objectUrl = url;
      if (cancelled) { URL.revokeObjectURL(url); return; }
      setState({ key, image: url });
    }).catch(() => { if (!cancelled) setState({ key, image: '' }); });
    return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [key]);
  return state.key === key ? state.image : '';
}
