import { PanelLeftOpen, PanelRightOpen } from "lucide-react";
import { Button } from "@/components/ui/button";

interface CompactToolbarProps {
  onLeftToggle: () => void;
  onRightToggle: () => void;
}

export function CompactToolbar({
  onLeftToggle,
  onRightToggle,
}: CompactToolbarProps) {
  return (
    <div className="flex h-10 shrink-0 items-center justify-between border-b bg-background px-3">
      <Button
        variant="ghost"
        size="sm"
        onClick={onLeftToggle}
        aria-label="Toggle tests panel"
      >
        <PanelLeftOpen className="h-5 w-5" />
        <span className="ml-1 text-base">Tests</span>
      </Button>

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
