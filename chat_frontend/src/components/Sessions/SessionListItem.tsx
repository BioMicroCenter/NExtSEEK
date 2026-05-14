import { useEffect, useRef, useState } from "react";
import { Pencil, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { SessionListItem as SessionListItemModel } from "@/lib/types/api";

interface SessionListItemProps {
  item: SessionListItemModel;
  active: boolean;
  disabled: boolean;
  collapsed?: boolean;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}

export function SessionListItem({
  item, active, disabled, collapsed = false,
  onSelect, onRename, onDelete,
}: SessionListItemProps) {
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(item.title);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (renaming) {
      setDraft(item.title);
      requestAnimationFrame(() => inputRef.current?.select());
    }
  }, [renaming, item.title]);

  const commitRename = () => {
    const trimmed = draft.trim();
    setRenaming(false);
    if (trimmed && trimmed !== item.title) {
      onRename(item.session_id, trimmed);
    }
  };
  const cancelRename = () => {
    setRenaming(false);
    setDraft(item.title);
  };

  return (
    <div
      className={[
        "group relative flex w-full min-w-0 items-center gap-1 overflow-hidden rounded-md px-2 py-1.5 text-sm",
        active ? "bg-accent text-accent-foreground" : "hover:bg-accent/50",
        disabled ? "opacity-60 cursor-not-allowed" : "cursor-pointer",
      ].join(" ")}
    >
      {renaming ? (
        <Input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") commitRename();
            else if (e.key === "Escape") cancelRename();
          }}
          onBlur={commitRename}
          className="h-7 flex-1"
          aria-label="Rename session"
        />
      ) : (
        <button
          type="button"
          onClick={() => !disabled && onSelect(item.session_id)}
          className="min-w-0 flex-1 truncate rounded-sm text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          title={item.title}
          disabled={disabled}
        >
          {collapsed ? item.title.slice(0, 1) : item.title}
        </button>
      )}

      {!collapsed && !renaming && (
        <div className="flex shrink-0 items-center gap-0.5">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            aria-label="Rename"
            title="Rename"
            onClick={(e) => { e.stopPropagation(); setRenaming(true); }}
            disabled={disabled}
          >
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7 hover:text-destructive"
            aria-label="Delete"
            title="Delete"
            onClick={(e) => { e.stopPropagation(); setConfirmOpen(true); }}
            disabled={disabled}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this chat?</DialogTitle>
            <DialogDescription>
              "{item.title}" will be permanently deleted, including its history. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => { setConfirmOpen(false); onDelete(item.session_id); }}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
