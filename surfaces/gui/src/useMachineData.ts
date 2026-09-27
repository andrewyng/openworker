import { useCallback, useEffect, useRef, useState } from "react";

// Data loading for machine-scoped Settings pages (UX-046). A remote read
// crosses the bridge and can be slow (or the box can be offline), so pages
// render in three honest states: fresh data · a cached copy while a refresh
// runs (with a visible timestamp) · a loading row when nothing is known yet.
// The cache is session-lifetime and in-memory only — nothing persists.

const cache = new Map<string, { data: unknown; at: number }>();

export interface MachineData<T> {
  data: T | null;
  /** Set when `data` came from the cache and a refresh is still in flight. */
  cachedAt: number | null;
  loading: boolean;
  error: boolean;
  refresh: () => void;
}

export function useMachineData<T>(key: string, fetcher: () => Promise<T>): MachineData<T> {
  const [state, setState] = useState<{ data: T | null; cachedAt: number | null; loading: boolean; error: boolean }>(
    () => {
      const hit = cache.get(key);
      return hit
        ? { data: hit.data as T, cachedAt: hit.at, loading: true, error: false }
        : { data: null, cachedAt: null, loading: true, error: false };
    },
  );
  const keyRef = useRef(key);
  keyRef.current = key;
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const load = useCallback(() => {
    const wanted = keyRef.current;
    const hit = cache.get(wanted);
    setState({
      data: (hit?.data as T) ?? null,
      cachedAt: hit?.at ?? null,
      loading: true,
      error: false,
    });
    fetcherRef.current().then(
      (data) => {
        cache.set(wanted, { data, at: Date.now() });
        if (keyRef.current === wanted)
          setState({ data, cachedAt: null, loading: false, error: false });
      },
      () => {
        if (keyRef.current === wanted)
          setState((prev) => ({ ...prev, loading: false, error: true }));
      },
    );
  }, []);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return { ...state, refresh: load };
}
