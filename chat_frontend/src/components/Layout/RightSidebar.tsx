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
import { Download } from "lucide-react";
import type { DebugData } from "@/lib/types/chat";

interface RightSidebarProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  debugData: DebugData;
  onDownload: (format: string) => void;
  isAdmin?: boolean;
}

export function RightSidebar({
  isOpen,
  onOpenChange,
  debugData,
  onDownload,
  isAdmin = false,
}: RightSidebarProps) {
  const hasBundle = debugData.bundleId !== null;

  return (
    <Sheet open={isOpen} onOpenChange={onOpenChange}>
      <SheetContent side="right">
        <SheetHeader>
          <SheetTitle>Debug Output</SheetTitle>
          <SheetDescription>Agent pipeline details</SheetDescription>
        </SheetHeader>

        <RouteOverrideSelect isAdmin={isAdmin} />
        <ProdToggle isAdmin={isAdmin} />

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
          >
            <Download className="mr-1 h-3 w-3" />
            JSON
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!hasBundle}
            onClick={() => onDownload("metadata")}
          >
            <Download className="mr-1 h-3 w-3" />
            Metadata
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
