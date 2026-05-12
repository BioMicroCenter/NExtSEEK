import { PanelLeftClose, PanelLeftOpen, MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { NewChatButton } from "./NewChatButton";
import { SessionListItem } from "./SessionListItem";
import type { SessionListItem as SessionListItemModel } from "@/lib/types/api";

interface SessionSidebarProps {
  sessions: SessionListItemModel[];
  activeSessionId: string | null;
  collapsed: boolean;
  inFlight: boolean;
  onNewChat: () => void;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onToggleCollapse: () => void;
}

export function SessionSidebar({
  sessions, activeSessionId, collapsed, inFlight,
  onNewChat, onSelect, onRename, onDelete, onToggleCollapse,
}: SessionSidebarProps) {
  const width = collapsed ? "w-12" : "w-[260px]";

  return (
    <aside
      className={`${width} shrink-0 border-r bg-background transition-[width] duration-150 flex flex-col`}
      aria-label="Saved chats"
    >
      <div className="flex items-center gap-2 p-2 border-b">
        <Button
          variant="ghost"
          size="icon"
          onClick={onToggleCollapse}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className="h-8 w-8"
        >
          {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
        </Button>
        {!collapsed && <span className="text-sm font-medium">Chats</span>}
      </div>

      <div className="p-2">
        <NewChatButton collapsed={collapsed} disabled={inFlight} onClick={onNewChat} />
      </div>

      <ScrollArea className="flex-1 px-2">
        {sessions.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center text-muted-foreground">
            <MessageSquare className="h-8 w-8 mb-2 opacity-50" />
            {!collapsed && <p className="text-sm">Start a new chat</p>}
          </div>
        ) : (
          <div className="space-y-1 pb-4">
            {sessions.map((s) => (
              <SessionListItem
                key={s.session_id}
                item={s}
                active={s.session_id === activeSessionId}
                disabled={inFlight}
                collapsed={collapsed}
                onSelect={onSelect}
                onRename={onRename}
                onDelete={onDelete}
              />
            ))}
          </div>
        )}
      </ScrollArea>
    </aside>
  );
}
