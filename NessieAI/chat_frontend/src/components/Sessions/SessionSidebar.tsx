import { useCallback, useEffect, useRef, useState } from "react";
import { MessageSquare, Search } from "lucide-react";
import { NewChatButton } from "./NewChatButton";
import { SessionListItem } from "./SessionListItem";
import { SessionSearchBox, SessionSearchResults } from "./SessionSearch";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { PHONE_QUERY, useMediaQuery } from "@/hooks/useMediaQuery";
import { useSessionSearch, type SessionSearchServiceLike } from "@/hooks/useSessionSearch";
import type { SessionListItem as SessionListItemModel } from "@/lib/types/api";

interface SessionSidebarProps {
  sessions: SessionListItemModel[];
  activeSessionId: string | null;
  collapsed: boolean;
  inFlight: boolean;
  /** Phones only: whether the saved-chats sheet is open. */
  sheetOpen?: boolean;
  onSheetOpenChange?: (open: boolean) => void;
  onNewChat: () => void;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  /** Without it the sidebar has no search box. */
  searchService?: SessionSearchServiceLike;
  /** Superusers get the all-users search filter (the server enforces it). */
  isAdmin?: boolean;
  /** Open a search hit: the chat, scrolled to the turn that matched (null: the top). */
  onOpenHit?: (sessionId: string, turnId: number | null) => void;
  /** Expand a collapsed sidebar, for Ctrl/Cmd+K and the collapsed search icon. */
  onExpand?: () => void;
}

// Cmd+K on a Mac (Ctrl+K there deletes to the end of the line); Ctrl+K elsewhere. Never with Shift or Alt.
const IS_MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
const isSearchShortcut = (e: KeyboardEvent) =>
  (IS_MAC ? e.metaKey : e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "k";

const NO_SEARCH: SessionSearchServiceLike = {
  searchSessions: () => Promise.reject(new Error("no search service")),
};

export function SessionSidebar({
  sessions, activeSessionId, collapsed, inFlight, sheetOpen = false, onSheetOpenChange,
  onNewChat, onSelect, onRename, onDelete, searchService, isAdmin = false, onOpenHit, onExpand,
}: SessionSidebarProps) {
  const isPhone = useMediaQuery(PHONE_QUERY);
  const close = () => onSheetOpenChange?.(false);
  // On a phone the list sits in a sheet that is never collapsed to icons.
  const narrow = collapsed && !isPhone;
  // Held here, not in the box: the phone sheet unmounts its content, and the query should outlive that.
  const search = useSessionSearch(searchService ?? NO_SEARCH);
  const [focusRequest, setFocusRequest] = useState(0);

  const openSearch = () => {
    if (isPhone) onSheetOpenChange?.(true);
    else if (narrow) onExpand?.();
    setFocusRequest((n) => n + 1);
  };
  const focusHandled = useCallback(() => setFocusRequest(0), []);
  const openSearchRef = useRef(openSearch);
  openSearchRef.current = openSearch;

  useEffect(() => {
    if (!searchService) return;
    const onKey = (e: KeyboardEvent) => {
      if (isSearchShortcut(e)) {
        e.preventDefault();
        openSearchRef.current();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [searchService]);

  const searching = Boolean(searchService) && !narrow && search.active;

  const content = (
    <>
      <div className="p-2">
        <NewChatButton
          collapsed={narrow}
          disabled={inFlight}
          onClick={() => { onNewChat(); if (isPhone) close(); }}
        />
      </div>

      {searchService && (narrow ? (
        <div className="px-2 pb-2">
          <Button type="button" variant="ghost" size="icon" className="h-8 w-8" aria-label="Search chats"
                  onClick={openSearch}>
            <Search className="h-4 w-4" />
          </Button>
        </div>
      ) : (
        <SessionSearchBox search={search} isAdmin={isAdmin} focusRequest={focusRequest} onFocused={focusHandled} />
      ))}

      <div className="min-w-0 flex-1 overflow-y-auto overflow-x-hidden px-2">
        {searching ? (
          <SessionSearchResults
            search={search}
            disabled={inFlight}
            onOpen={(id, turn) => { onOpenHit?.(id, turn); if (isPhone) close(); }}
          />
        ) : sessions.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center text-muted-foreground">
            <MessageSquare className="h-8 w-8 mb-2 opacity-50" />
            {!narrow && <p className="text-sm">Start a new chat</p>}
          </div>
        ) : (
          <div className="min-w-0 space-y-1 pb-4">
            {sessions.map((s) => (
              <SessionListItem
                key={s.session_id}
                item={s}
                active={s.session_id === activeSessionId}
                disabled={inFlight}
                collapsed={narrow}
                onSelect={(id) => { onSelect(id); if (isPhone) close(); }}
                onRename={onRename}
                onDelete={onDelete}
              />
            ))}
          </div>
        )}
      </div>
    </>
  );

  if (isPhone) {
    return (
      <Sheet open={sheetOpen} onOpenChange={onSheetOpenChange}>
        <SheetContent side="left" aria-label="Saved chats" className="gap-0 p-0">
          <SheetHeader className="pb-0">
            <SheetTitle>Saved chats</SheetTitle>
            <SheetDescription className="sr-only">Pick a saved chat or start a new one.</SheetDescription>
          </SheetHeader>
          {content}
        </SheetContent>
      </Sheet>
    );
  }

  const width = narrow ? "w-12" : "w-[260px]";
  return (
    <aside
      className={`${width} shrink-0 overflow-hidden border-r bg-background transition-[width] duration-150 flex flex-col`}
      aria-label="Saved chats"
    >
      {content}
    </aside>
  );
}
