import { useCallback, useEffect, useRef, useState } from "react";
import type { SessionSearchHit, SessionSearchOptions, SessionSearchResponse } from "@/lib/types/api";

export const SEARCH_DEBOUNCE_MS = 250;
export const SEARCH_MIN_CHARS = 2;

export interface SessionSearchServiceLike {
  searchSessions: (q: string, opts: SessionSearchOptions, signal?: AbortSignal) => Promise<SessionSearchResponse>;
}

export interface SearchFilters {
  sinceDays: 7 | 30 | 90 | null;
  allUsers: boolean;
}

export const DEFAULT_FILTERS: SearchFilters = { sinceDays: null, allUsers: false };

export interface UseSessionSearchReturn {
  query: string;
  setQuery: (q: string) => void;
  filters: SearchFilters;
  setFilters: (f: SearchFilters) => void;
  /** True while the box holds enough to search: the sidebar shows results instead of the list. */
  active: boolean;
  results: SessionSearchHit[];
  terms: string[];
  loading: boolean;
  error: string | null;
  clear: () => void;
}

/** The sidebar's search: waits for the typing to stop, cancels the request a newer one replaces. */
export function useSessionSearch(service: SessionSearchServiceLike): UseSessionSearchReturn {
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<SearchFilters>(DEFAULT_FILTERS);
  const [results, setResults] = useState<SessionSearchHit[]>([]);
  const [terms, setTerms] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef<AbortController | null>(null);

  const q = query.trim();
  const active = q.length >= SEARCH_MIN_CHARS;

  useEffect(() => {
    if (!active) {
      inFlight.current?.abort();
      setResults([]);
      setTerms([]);
      setLoading(false);
      setError(null);
      return;
    }
    const timer = setTimeout(() => {
      inFlight.current?.abort();
      const controller = new AbortController();
      inFlight.current = controller;
      setLoading(true);
      service
        .searchSessions(q, filters, controller.signal)
        .then((res) => {
          if (controller.signal.aborted) return;
          setResults(res.results);
          setTerms(res.terms);
          setError(null);
        })
        .catch(() => {
          if (controller.signal.aborted) return;
          setError("Search failed.");
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [q, active, filters, service]);

  useEffect(() => () => inFlight.current?.abort(), []);

  const clear = useCallback(() => setQuery(""), []);

  return { query, setQuery, filters, setFilters, active, results, terms, loading, error, clear };
}
