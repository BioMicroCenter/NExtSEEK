import { PanelRightOpen } from "lucide-react";
import { Button } from "@/components/ui/button";

interface CompactToolbarProps {
  onRightToggle: () => void;
}

export function CompactToolbar({
  onRightToggle,
}: CompactToolbarProps) {
  return (
    <div className="flex h-10 shrink-0 items-center justify-end border-b bg-background px-3">
      <Button
        variant="ghost"
        size="sm"
        onClick={onRightToggle}
        aria-label="Toggle debug panel"
      >
        <span className="mr-1 text-base">Debug</span>
        <PanelRightOpen className="h-5 w-5" />
      </Button>
    </div>
  );
}
