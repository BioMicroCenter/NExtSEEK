import { AlertCircle, FileEdit, FilePlus, Terminal, Wrench } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { CCTrace, CCTraceStep } from "@/lib/types/chat";

interface CCActivityPanelProps {
  trace: CCTrace;
}

function kindIcon(kind: CCTraceStep["kind"]) {
  switch (kind) {
    case "bash":
      return <Terminal className="h-3 w-3" />;
    case "write":
    case "edit":
      return <FileEdit className="h-3 w-3" />;
    case "read":
      return <FilePlus className="h-3 w-3" />;
    default:
      return <Wrench className="h-3 w-3" />;
  }
}

function formatDuration(ms?: number): string {
  if (ms == null) return "";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatCost(usd?: number): string {
  if (usd == null) return "";
  return `$${usd.toFixed(4)}`;
}

export function CCActivityPanel({ trace }: CCActivityPanelProps) {
  const headerParts = [
    trace.num_turns != null ? `${trace.num_turns} turns` : null,
    `${trace.turn_count} CC turn${trace.turn_count === 1 ? "" : "s"}`,
    trace.duration_ms != null ? formatDuration(trace.duration_ms) : null,
    trace.cost_usd != null ? formatCost(trace.cost_usd) : null,
  ].filter(Boolean);

  const toolEntries = Object.entries(trace.tools_used ?? {});

  return (
    <div className="space-y-3 text-xs" data-testid="cc-activity-panel">
      {headerParts.length > 0 && (
        <p className="font-medium text-muted-foreground">{headerParts.join(" · ")}</p>
      )}

      {trace.steps.length > 0 && (
        <div className="space-y-1.5">
          {trace.steps.map((step, i) => (
            <div key={`${step.line}-${i}`} className="flex items-start gap-2">
              <span className="mt-0.5 shrink-0 text-muted-foreground">{kindIcon(step.kind)}</span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  {step.tool && (
                    <Badge variant="secondary" className="text-[10px] font-mono">
                      {step.tool}
                    </Badge>
                  )}
                  {step.status === "error" && (
                    <Badge variant="destructive" className="gap-0.5 text-[10px]">
                      <AlertCircle className="h-2.5 w-2.5" />
                      error
                    </Badge>
                  )}
                </div>
                {(step.detail || step.text || step.action) && (
                  <p className="mt-0.5 break-all font-mono text-muted-foreground">
                    {step.detail ?? step.text ?? step.action}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {(trace.files_created.length > 0 || trace.files_modified.length > 0) && (
        <div className="space-y-1 border-t border-border/40 pt-2">
          {trace.files_created.map((f) => (
            <p key={`c-${f}`} className="font-mono text-muted-foreground">
              {f}
            </p>
          ))}
          {trace.files_modified.map((f) => (
            <p key={`m-${f}`} className="font-mono text-muted-foreground">
              {f}
            </p>
          ))}
        </div>
      )}

      {toolEntries.length > 0 && (
        <div className="flex flex-wrap gap-1 border-t border-border/40 pt-2">
          {toolEntries.map(([tool, count]) => (
            <Badge key={tool} variant="outline" className="text-[10px]">
              {tool}: {count}
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}
