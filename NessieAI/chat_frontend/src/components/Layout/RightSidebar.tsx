import { useCallback, useRef, useState } from "react";
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

/**
 * How long "All files" stays disabled once its download has been handed over.
 * The embedded shell hands the browser a link and returns at once, while the
 * server is still planning the zip, so this hold is what makes a double click
 * one download there.
 */
export const DOWNLOAD_ALL_HOLD_MS = 3000;

interface RightSidebarProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  debugData: DebugData;
  onDownload: (format: string) => void;
  /** The chat on screen. "All files" keys on it, never on `debugData.bundleId`. */
  activeSessionId?: string | null;
  /** Settles once the download is handed over; the button stays disabled until then. */
  onDownloadAll?: () => Promise<unknown> | void;
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
  const [downloadingAll, setDownloadingAll] = useState(false);
  // The state reaches the button a render later; the ref stops a second click now.
  const downloadingAllRef = useRef(false);

  const handleDownloadAll = useCallback(() => {
    if (!onDownloadAll || downloadingAllRef.current) return;
    downloadingAllRef.current = true;
    setDownloadingAll(true);
    const release = () => {
      setTimeout(() => {
        downloadingAllRef.current = false;
        setDownloadingAll(false);
      }, DOWNLOAD_ALL_HOLD_MS);
    };
    // The shells report a failure themselves; here the button only has to come back.
    new Promise((resolve) => resolve(onDownloadAll())).then(release, release);
  }, [onDownloadAll]);

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
            disabled={!canDownloadAll || downloadingAll}
            aria-busy={downloadingAll}
            onClick={handleDownloadAll}
            title="This chat's transcript and every file its turns produced, as one zip"
            data-testid="session-download"
          >
            <FolderDown className="mr-1 h-3 w-3" />
            {downloadingAll ? "Downloading…" : "All files"}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
