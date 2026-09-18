import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { DebugPanel } from "@/components/DebugPanel/DebugPanel";
import { RouteOverrideSelect } from "./RouteOverrideSelect";
import { ProdToggle } from "./ProdToggle";
import { MaxTurnLengthInput } from "./MaxTurnLengthInput";
import { Download, FolderDown } from "lucide-react";
import type { DebugData } from "@/lib/types/chat";

interface RightSidebarProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  debugData: DebugData;
  onDownload: (format: string) => void;
  /** The chat on screen. "All files" keys on it, never on `debugData.bundleId`. */
  activeSessionId?: string | null;
  onDownloadAll?: () => void;
  isAdmin?: boolean;
}

export function RightSidebar({
  isOpen,
  onOpenChange,
  debugData,
  onDownload,
  activeSessionId = null,
  onDownloadAll,
  isAdmin = false,
}: RightSidebarProps) {
  const hasBundle = debugData.bundleId !== null;
  // Not hasBundle: the panel is anchored to the newest turn (debugForTurns), so
  // a chat whose newest turn wrote no bundle would get a dead button while its
  // older turns still hold files.
  const canDownloadAll = Boolean(activeSessionId && onDownloadAll);

  return (
    <Sheet open={isOpen} onOpenChange={onOpenChange}>
      <SheetContent side="right">
        <SheetHeader>
          <SheetTitle>Debug Output</SheetTitle>
          <SheetDescription>Agent pipeline details</SheetDescription>
        </SheetHeader>

        <RouteOverrideSelect isAdmin={isAdmin} />
        <ProdToggle isAdmin={isAdmin} />
        <MaxTurnLengthInput isAdmin={isAdmin} />

        <ScrollArea className="flex-1 px-4">
          <DebugPanel debugData={debugData} />
        </ScrollArea>

        <Separator />

        <div className="flex gap-2 p-4">
          <Button
            variant="outline"
            size="sm"
            disabled={!hasBundle}
            onClick={() => onDownload("json")}
            data-testid="json-download"
          >
            <Download className="mr-1 h-3 w-3" />
            JSON
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!hasBundle}
            onClick={() => onDownload("metadata")}
            data-testid="metadata-download"
          >
            <Download className="mr-1 h-3 w-3" />
            Metadata
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!canDownloadAll}
            onClick={() => onDownloadAll?.()}
            title="This chat's transcript and every file its turns produced, as one zip"
            data-testid="session-download"
          >
            <FolderDown className="mr-1 h-3 w-3" />
            All files
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
