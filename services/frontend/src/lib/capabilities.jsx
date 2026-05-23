import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { fetchCapabilities } from '../api';

export const DEFAULT_CAPABILITIES = Object.freeze({
  features: {
    avatar: { enabled: false, reason: 'Unavailable until deployment capabilities load.' },
    intelligence: { enabled: false, reason: 'Unavailable until deployment capabilities load.' },
    local_ollama: { enabled: false, reason: 'Unavailable until deployment capabilities load.' },
    visual_moderation: { enabled: false, reason: 'Unavailable until deployment capabilities load.' },
    local_tts: { enabled: true, status: 'unknown' },
  },
});

function normalizeFeature(value, fallback) {
  if (!value || typeof value !== 'object') {
    return { ...fallback };
  }
  return {
    ...fallback,
    ...value,
    enabled: Boolean(value.enabled),
  };
}

export function normalizeCapabilities(payload) {
  const source = payload && typeof payload === 'object' ? payload : {};
  const features = source.features && typeof source.features === 'object' ? source.features : {};
  return {
    ...source,
    features: {
      avatar: normalizeFeature(features.avatar, DEFAULT_CAPABILITIES.features.avatar),
      intelligence: normalizeFeature(features.intelligence, DEFAULT_CAPABILITIES.features.intelligence),
      local_ollama: normalizeFeature(features.local_ollama, DEFAULT_CAPABILITIES.features.local_ollama),
      visual_moderation: normalizeFeature(features.visual_moderation, DEFAULT_CAPABILITIES.features.visual_moderation),
      local_tts: normalizeFeature(features.local_tts, DEFAULT_CAPABILITIES.features.local_tts),
    },
  };
}

const CapabilitiesContext = createContext({
  capabilities: DEFAULT_CAPABILITIES,
  capabilitiesLoading: false,
  capabilitiesError: null,
  refreshCapabilities: async () => DEFAULT_CAPABILITIES,
});

export function featureEnabled(capabilities, featureName) {
  return Boolean(capabilities?.features?.[featureName]?.enabled);
}

export function featureReason(capabilities, featureName) {
  return String(capabilities?.features?.[featureName]?.reason || '').trim();
}

export function featureStatusLabel(capabilities, featureName, enabledLabel = 'Enabled', disabledLabel = 'Disabled') {
  return featureEnabled(capabilities, featureName) ? enabledLabel : disabledLabel;
}

export function CapabilitiesProvider({ children }) {
  const [capabilities, setCapabilities] = useState(DEFAULT_CAPABILITIES);
  const [capabilitiesLoading, setCapabilitiesLoading] = useState(true);
  const [capabilitiesError, setCapabilitiesError] = useState(null);
  const requestIdRef = useRef(0);

  const refreshCapabilities = useCallback(async ({ force = false } = {}) => {
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    const isCurrentRequest = () => requestIdRef.current === requestId;

    try {
      setCapabilitiesLoading(true);
      const payload = await fetchCapabilities({ force });
      const normalized = normalizeCapabilities(payload);
      if (isCurrentRequest()) {
        setCapabilities(normalized);
        setCapabilitiesError(null);
      }
      return normalized;
    } catch (error) {
      if (isCurrentRequest()) {
        setCapabilities(DEFAULT_CAPABILITIES);
        setCapabilitiesError(error);
      }
      return DEFAULT_CAPABILITIES;
    } finally {
      if (isCurrentRequest()) {
        setCapabilitiesLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    refreshCapabilities({ force: true });
  }, [refreshCapabilities]);

  const value = useMemo(() => ({
    capabilities,
    capabilitiesLoading,
    capabilitiesError,
    refreshCapabilities,
  }), [capabilities, capabilitiesError, capabilitiesLoading, refreshCapabilities]);

  return (
    <CapabilitiesContext.Provider value={value}>
      {children}
    </CapabilitiesContext.Provider>
  );
}

export function useCapabilities() {
  return useContext(CapabilitiesContext);
}
