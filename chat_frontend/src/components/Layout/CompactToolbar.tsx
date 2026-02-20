import { PanelLeftOpen } from "lucide-react";
import { Button } from "@/components/ui/button";

interface CompactToolbarProps {
  onLeftToggle: () => void;
}

export function CompactToolbar({
  onLeftToggle,
}: CompactToolbarProps) {
  return (
    <div className="flex h-10 shrink-0 items-center justify-start border-b bg-background px-3">
      <Button
        variant="ghost"
        size="sm"
        onClick={onLeftToggle}
        aria-label="Toggle tests panel"
      >
        <PanelLeftOpen className="h-5 w-5" />
        <span className="ml-1 text-base">Tests</span>
      </Button>
    </div>
  );
}
