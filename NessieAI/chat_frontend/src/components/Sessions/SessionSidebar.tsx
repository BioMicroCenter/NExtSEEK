import { MessageSquare } from "lucide-react";
import { NewChatButton } from "./NewChatButton";
import { SessionListItem } from "./SessionListItem";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { PHONE_QUERY, useMediaQuery } from "@/hooks/useMediaQuery";
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
}

export function SessionSidebar({
  sessions, activeSessionId, collapsed, inFlight, sheetOpen = false, onSheetOpenChange,
  onNewChat, onSelect, onRename, onDelete,
}: SessionSidebarProps) {
  const isPhone = useMediaQuery(PHONE_QUERY);
  const close = () => onSheetOpenChange?.(false);
  // On a phone the list sits in a sheet that is never collapsed to icons.
  const narrow = collapsed && !isPhone;

  const content = (
    <>
      <div className="p-2">
        <NewChatButton
          collapsed={narrow}
          disabled={inFlight}
          onClick={() => { onNewChat(); if (isPhone) close(); }}
        />
      </div>

      <div className="min-w-0 flex-1 overflow-y-auto overflow-x-hidden px-2">
        {sessions.length === 0 ? (
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
