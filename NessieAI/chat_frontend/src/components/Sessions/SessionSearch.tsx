import { useEffect, useRef, useState } from "react";
import { Search, SlidersHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { highlightTerms } from "@/lib/highlight";
import { findUids, getUrlForUid } from "@/lib/remark-uid-links";
import { DEFAULT_FILTERS, type SearchFilters, type UseSessionSearchReturn } from "@/hooks/useSessionSearch";
import type { RecordWhere, SessionSearchHit } from "@/lib/types/api";

interface SessionSearchBoxProps {
  search: UseSessionSearchReturn;
  isAdmin: boolean;
  /** Bumped by Ctrl/Cmd+K: a non-zero value focuses the box, which then calls `onFocused`. */
  focusRequest: number;
  onFocused: () => void;
}

/** The search box above the saved chats, and its options panel: what to search (chats and records always, the
 *  agent's steps when ticked), the record filters with their counts, the date and, for superusers, all users. */
export function SessionSearchBox({ search, isAdmin, focusRequest, onFocused }: SessionSearchBoxProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [optionsOpen, setOptionsOpen] = useState(false);
  const { filters, setFilters, active, loadFacets, setCountsWanted } = search;
  const filtered = (Object.keys(DEFAULT_FILTERS) as (keyof SearchFilters)[])
    .some((k) => filters[k] !== DEFAULT_FILTERS[k]);

  // With nothing searched yet, the counts beside the record filters cover every chat in scope.
  useEffect(() => {
    if (optionsOpen && !active) loadFacets();
  }, [optionsOpen, active, loadFacets]);
  // The counts are asked for only while the panel shows them.
  useEffect(() => setCountsWanted(optionsOpen), [optionsOpen, setCountsWanted]);

  useEffect(() => {
    if (!focusRequest) return;
    // After a sheet or a collapsed sidebar has opened and rendered the box.
    const t = setTimeout(() => {
      inputRef.current?.focus();
      onFocused(); // once per Ctrl/Cmd+K: a later remount (sheet, expanded sidebar) does not grab focus again
    }, 0);
    return () => clearTimeout(t);
  }, [focusRequest, onFocused]);

  const set = (patch: Partial<SearchFilters>) => setFilters({ ...filters, ...patch });

  return (
    <div className="px-2 pb-2">
      <div className="flex items-center gap-1">
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            ref={inputRef}
            type="search"
            value={search.query}
            onChange={(e) => search.setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== "Escape") return;
              if (search.query) search.clear();
              else inputRef.current?.blur();
            }}
            maxLength={200}
            placeholder="Search chats"
            aria-label="Search chats"
            data-testid="session-search"
            className="h-8 pl-7 text-sm"
          />
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="relative h-8 w-8 shrink-0"
          aria-label="Search options"
          aria-expanded={optionsOpen}
          onClick={() => setOptionsOpen((o) => !o)}
        >
          <SlidersHorizontal className="h-4 w-4" />
          {filtered && <span data-filter-dot className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-primary" />}
        </Button>
      </div>
      {optionsOpen && (
        <div className="mt-2 space-y-2 rounded-md border p-2 text-xs">
          <div className="flex items-center justify-between gap-2">
            <span className="text-muted-foreground">Search in</span>
            <span>Chats, Records</span>
          </div>
          <label className="flex items-center justify-between gap-2">
            <span className="text-muted-foreground">Agent steps</span>
            <input
              type="checkbox"
              aria-label="Agent steps"
              checked={filters.steps}
              onChange={(e) => set({ steps: e.target.checked })}
            />
          </label>
          <FacetSelect label="Record type" value={filters.recordType} counts={search.facets.record_type}
                       onChange={(v) => set({ recordType: v })} />
          <FacetSelect label="Project" value={filters.project} counts={search.facets.project}
                       onChange={(v) => set({ project: v })} />
          <FacetSelect label="Where" value={filters.where} counts={search.facets.where}
                       onChange={(v) => set({ where: v as RecordWhere | null })} />
          <label className="flex items-center justify-between gap-2">
            <span className="text-muted-foreground">When</span>
            <select
              aria-label="When"
              className="rounded-sm border bg-background px-1 py-0.5"
              value={filters.sinceDays ?? ""}
              onChange={(e) => set({ sinceDays: e.target.value ? (Number(e.target.value) as 7 | 30 | 90) : null })}
            >
              <option value="">Any time</option>
              <option value="7">Past 7 days</option>
              <option value="30">Past 30 days</option>
              <option value="90">Past 90 days</option>
            </select>
          </label>
          {isAdmin && (
            <label className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">All users</span>
              <input
                type="checkbox"
                aria-label="All users"
                checked={filters.allUsers}
                onChange={(e) => set({ allUsers: e.target.checked })}
              />
            </label>
          )}
        </div>
      )}
    </div>
  );
}

interface FacetSelectProps {
  label: string;
  value: string | null;
  counts: Record<string, number>;
  onChange: (value: string | null) => void;
}

/** One record filter: "Any", then each value with how many matching chats it keeps (the chosen one always shown). */
function FacetSelect({ label, value, counts, onChange }: FacetSelectProps) {
  const entries = Object.entries(counts);
  if (value && !(value in counts)) entries.push([value, 0]);
  return (
    <label className="flex items-center justify-between gap-2">
      <span className="text-muted-foreground">{label}</span>
      <select
        aria-label={label}
        className="max-w-[9rem] rounded-sm border bg-background px-1 py-0.5"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
      >
        <option value="">Any</option>
        {entries.map(([v, n]) => <option key={v} value={v}>{`${v} (${n})`}</option>)}
      </select>
    </label>
  );
}

/** A record hit's sample IDs as links to their pages, outside the result's button (a link may not sit in one).
 *  Protocol IDs stay text: the page their link would name has no route (chat_frontend/CLAUDE.md). */
function RecordLinks({ snippet }: { snippet: string }) {
  const samples = findUids(snippet).filter((uid) => !uid.startsWith("P."));
  if (!samples.length) return null;
  return (
    <span className="flex flex-wrap gap-x-2 px-2 pb-1 text-[11px]">
      {samples.map((uid) => (
        <a key={uid} href={getUrlForUid(uid)} target="_blank" rel="noopener noreferrer"
           className="text-primary underline-offset-2 hover:underline">{uid}</a>
      ))}
    </span>
  );
}

const SOURCE_LABEL: Record<string, string> = { record: "in the records", steps: "in the agent's steps" };

interface SessionSearchResultsProps {
  search: UseSessionSearchReturn;
  disabled: boolean;
  onOpen: (sessionId: string, turnId: number | null, source: SessionSearchHit["source"]) => void;
}

const day = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

/** What replaces the saved-chats list while the box holds a search. */
export function SessionSearchResults({ search, disabled, onOpen }: SessionSearchResultsProps) {
  const { results, terms, loading, error } = search;
  if (error) return <p className="px-2 py-4 text-center text-sm text-destructive">{error}</p>;
  if (!results.length) {
    return (
      <p className="px-2 py-4 text-center text-sm text-muted-foreground">
        {loading ? "Searching…" : "No chats match"}
      </p>
    );
  }
  return (
    <div className="min-w-0 space-y-1 pb-4" aria-label="Search results">
      {results.map((h) => (
        <div key={h.session_id} className="min-w-0">
          <button
            type="button"
            data-testid="session-search-result"
            data-session-id={h.session_id}
            aria-disabled={disabled}
            onClick={() => !disabled && onOpen(h.session_id, h.turn_id, h.source)}
            className={[
              "block w-full min-w-0 rounded-md px-2 py-1.5 text-left text-sm ring-offset-background",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
              disabled ? "cursor-not-allowed opacity-60" : "hover:bg-accent/50",
            ].join(" ")}
          >
            <span className="flex min-w-0 items-baseline gap-2">
              <span className="min-w-0 flex-1 truncate font-medium">{highlightTerms(h.title, terms)}</span>
              <span className="shrink-0 text-xs text-muted-foreground">{day(h.updated_at)}</span>
            </span>
            {h.owner && <span className="block truncate text-xs text-muted-foreground">{h.owner}</span>}
            {SOURCE_LABEL[h.source] && (
              <span className="block text-[11px] italic text-muted-foreground">{SOURCE_LABEL[h.source]}</span>
            )}
            {h.snippet && (
              <span className="mt-0.5 line-clamp-2 block text-xs text-muted-foreground">
                {highlightTerms(h.snippet, terms)}
              </span>
            )}
          </button>
          {h.source === "record" && <RecordLinks snippet={h.snippet} />}
        </div>
      ))}
    </div>
  );
}
