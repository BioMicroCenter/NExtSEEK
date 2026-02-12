import { format } from "date-fns";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import type { DebugData } from "@/lib/types/chat";

interface DebugPanelProps {
  debugData: DebugData;
}

export function DebugPanel({ debugData }: DebugPanelProps) {
  if (debugData.entries.length === 0) {
    return (
      <div className="flex h-32 items-center justify-center">
        <p className="text-sm text-muted-foreground">
          Send a query to see debug output
        </p>
      </div>
    );
  }

  return (
    <Accordion type="multiple">
      {debugData.entries.map((entry, i) => (
        <AccordionItem key={`${entry.agent}-${i}`} value={`${entry.agent}-${i}`}>
          <AccordionTrigger className="text-sm">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="text-xs">
                {entry.agent}
              </Badge>
              <span className="text-xs text-muted-foreground">
                {format(entry.timestamp, "HH:mm:ss")}
              </span>
            </div>
          </AccordionTrigger>
          <AccordionContent>
            <pre className="whitespace-pre-wrap text-xs text-muted-foreground">
              {entry.summary}
            </pre>
          </AccordionContent>
        </AccordionItem>
      ))}
    </Accordion>
  );
}
