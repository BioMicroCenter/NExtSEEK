import { useCallback, useEffect, useRef, useState } from "react";
import type {
  RecordWhere,
  SessionSearchFacets,
  SessionSearchHit,
  SessionSearchOptions,
  SessionSearchResponse,
} from "@/lib/types/api";

export const SEARCH_DEBOUNCE_MS = 250;
export const SEARCH_MIN_CHARS = 2;

export interface SessionSearchServiceLike {
  searchSessions: (q: string, opts: SessionSearchOptions, signal?: AbortSignal) => Promise<SessionSearchResponse>;
}

export interface SearchFilters {
  sinceDays: 7 | 30 | 90 | null;
  allUsers: boolean;
  /** Also search the Container-CC agent's steps. */
  steps: boolean;
  recordType: string | null;
  project: string | null;
  where: RecordWhere | null;
}

export const DEFAULT_FILTERS: SearchFilters = {
  sinceDays: null, allUsers: false, steps: false, recordType: null, project: null, where: null,
};

const NO_FACETS: SessionSearchFacets = { record_type: {}, project: {}, where: {} };

/** A record filter narrows to chats that showed such a record, and needs no words. */
export const hasRecordFilter = (f: SearchFilters) => Boolean(f.recordType || f.project || f.where);

export interface UseSessionSearchReturn {
  query: string;
  setQuery: (q: string) => void;
  filters: SearchFilters;
  setFilters: (f: SearchFilters) => void;
  /** True while there is something to search (enough words, or a record filter): the sidebar shows results. */
  active: boolean;
  results: SessionSearchHit[];
  terms: string[];
  /** The counts beside each filter value: from the last search, or from `loadFacets`. */
  facets: SessionSearchFacets;
  loadFacets: () => void;
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
  const [facets, setFacets] = useState<SessionSearchFacets>(NO_FACETS);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef<AbortController | null>(null);
  // Bumped by every search and every counts-only call: only the newest may set the counts.
  const countsSeq = useRef(0);

  const trimmed = query.trim();
  const q = trimmed.length >= SEARCH_MIN_CHARS ? trimmed : "";
  const active = Boolean(q) || hasRecordFilter(filters);

  useEffect(() => {
    if (!active) {
      inFlight.current?.abort();
      setResults([]);
      setTerms([]);
      setLoading(false);
      setError(null);
      return;
    }
    setLoading(true); // "Searching…", not "No chats match", while the typing settles
    const timer = setTimeout(() => {
      const seq = ++countsSeq.current;
      inFlight.current?.abort();
      const controller = new AbortController();
      inFlight.current = controller;
      service
        .searchSessions(q, filters, controller.signal)
        .then((res) => {
          if (controller.signal.aborted) return;
          setResults(res.results);
          setTerms(res.terms);
          if (seq === countsSeq.current) setFacets(res.facets ?? NO_FACETS);
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

  const { sinceDays, allUsers } = filters;
  const loadFacets = useCallback(() => {
    const seq = ++countsSeq.current;
    service
      .searchSessions("", { sinceDays, allUsers, facetsOnly: true })
      .then((res) => { if (seq === countsSeq.current) setFacets(res.facets ?? NO_FACETS); })
      .catch(() => { /* the filters still work; only their counts are missing */ });
  }, [service, sinceDays, allUsers]);

  const clear = useCallback(() => setQuery(""), []);

  return { query, setQuery, filters, setFilters, active, results, terms, facets, loadFacets, loading, error, clear };
}
