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
      <div data-testid="debug-panel" className="flex h-32 items-center justify-center">
        <p className="text-base text-muted-foreground">
          Send a query to see debug output
        </p>
      </div>
    );
  }

  return (
    <Accordion type="multiple" data-testid="debug-panel">
      {debugData.entries.map((entry, i) => (
        <AccordionItem
          key={`${entry.agent}-${i}`}
          value={`${entry.agent}-${i}`}
          data-testid="debug-entry"
          data-agent={entry.agent}
        >
          <AccordionTrigger className="text-base">
            <div className="flex flex-wrap items-center gap-2 text-left">
              <Badge variant="outline" className="text-sm">
                {entry.agent}
              </Badge>
              <span className="text-sm text-muted-foreground">
                {format(entry.timestamp, "HH:mm:ss")}
              </span>
              {/* The route is the point of the router entry: it shows closed, not only once opened. */}
              {entry.agent === "router" ? (
                <span className="text-sm font-normal">{entry.summary.split("\n")[0]}</span>
              ) : null}
            </div>
          </AccordionTrigger>
          <AccordionContent>
            <pre className="whitespace-pre-wrap text-sm text-muted-foreground">
              {entry.summary}
            </pre>
          </AccordionContent>
        </AccordionItem>
      ))}
    </Accordion>
  );
}
