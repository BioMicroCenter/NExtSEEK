import { useEffect, useRef, useState } from "react";
import { MoreHorizontal } from "lucide-react";
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
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(item.title);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (renaming) {
      setDraft(item.title);
      requestAnimationFrame(() => inputRef.current?.select());
    }
  }, [renaming, item.title]);

  useEffect(() => {
    if (!menuOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [menuOpen]);

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
        "group relative flex items-center gap-2 rounded-md px-2 py-1.5 text-sm",
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
          className="flex-1 truncate text-left"
          title={item.title}
          disabled={disabled}
        >
          {collapsed ? item.title.slice(0, 1) : item.title}
        </button>
      )}

      {!collapsed && !renaming && (
        <div className="relative" ref={menuRef}>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-6 w-6 opacity-0 group-hover:opacity-100"
            aria-label="More"
            onClick={() => setMenuOpen((v) => !v)}
            disabled={disabled}
          >
            <MoreHorizontal className="h-4 w-4" />
          </Button>
          {menuOpen && (
            <div
              role="menu"
              className="absolute right-0 top-full z-50 mt-1 w-32 rounded-md border bg-popover p-1 text-popover-foreground shadow-md"
            >
              <button
                type="button"
                role="menuitem"
                className="w-full rounded-sm px-2 py-1 text-left text-sm hover:bg-accent"
                onClick={() => { setMenuOpen(false); setRenaming(true); }}
              >
                Rename
              </button>
              <button
                type="button"
                role="menuitem"
                className="w-full rounded-sm px-2 py-1 text-left text-sm hover:bg-accent"
                onClick={() => { setMenuOpen(false); setConfirmOpen(true); }}
              >
                Delete
              </button>
            </div>
          )}
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
