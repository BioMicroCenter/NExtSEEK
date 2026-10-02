import { CircleHelp, Menu, PanelLeft, PanelRightOpen } from "lucide-react";
import { Button } from "@/components/ui/button";

interface CompactToolbarProps {
  onRightToggle: () => void;
  onLeftToggle: () => void;
  onAboutOpen: () => void;
}

declare global {
  interface Window {
    /** Opens the site's own drawer menu; defined by the site's nextseek.js. */
    openSidebar?: () => void;
  }
}

const COARSE = "[@media(pointer:coarse)]:min-h-11 [@media(pointer:coarse)]:min-w-11";

export function CompactToolbar({ onRightToggle, onLeftToggle, onAboutOpen }: CompactToolbarProps) {
  return (
    <div className="flex h-10 shrink-0 items-center border-b bg-background px-3 [@media(pointer:coarse)]:h-12">
      {/* The page hides the site's hamburger below 992px; this is its stand-in. */}
      <Button
        variant="ghost"
        size="sm"
        onClick={() => window.openSidebar?.()}
        aria-label="Site menu"
        className={`mr-1 hidden max-[991.98px]:inline-flex ${COARSE}`}
      >
        <Menu className="h-4 w-4" />
      </Button>
      <Button
        variant="ghost"
        size="sm"
        onClick={onLeftToggle}
        aria-label="Toggle chat list"
        className={`mr-2 ${COARSE}`}
      >
        <PanelLeft className="h-4 w-4" />
      </Button>
      <div className="flex-1" />
      <Button variant="ghost" size="sm" onClick={onAboutOpen} aria-label="About Nessie" className={`mr-1 ${COARSE}`}>
        <span className="mr-1 hidden text-base md:inline">About</span>
        <CircleHelp className="h-5 w-5" />
      </Button>
      <Button variant="ghost" size="sm" onClick={onRightToggle} aria-label="Toggle debug panel" className={COARSE}>
        <span className="mr-1 hidden text-base md:inline">Debug</span>
        <PanelRightOpen className="h-5 w-5" />
      </Button>
    </div>
  );
}
