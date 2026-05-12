import { PanelLeft, PanelRightOpen } from "lucide-react";
import { Button } from "@/components/ui/button";

interface CompactToolbarProps {
  onRightToggle: () => void;
  onLeftToggle: () => void;
}

export function CompactToolbar({ onRightToggle, onLeftToggle }: CompactToolbarProps) {
  return (
    <div className="flex h-10 shrink-0 items-center border-b bg-background px-3">
      <Button
        variant="ghost"
        size="sm"
        onClick={onLeftToggle}
        aria-label="Toggle chat list"
        className="mr-2"
      >
        <PanelLeft className="h-4 w-4" />
      </Button>
      <div className="flex-1" />
      <Button variant="ghost" size="sm" onClick={onRightToggle} aria-label="Toggle debug panel">
        <span className="mr-1 text-base">Debug</span>
        <PanelRightOpen className="h-5 w-5" />
      </Button>
    </div>
  );
}
