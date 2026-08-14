import { useState } from "react";
import {
  Brain,
  Route,
  Code,
  Globe,
  MessageSquare,
  Database,
  BookOpen,
  Network,
  FileDown,
  Terminal,
  FileText,
  Search,
  Check,
  Loader2,
  ChevronDown,
  AlertCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { Step } from "@/lib/types/chat";

const AGENT_ICONS: Record<string, React.ElementType> = {
  // NS pipeline agents
  entity: Brain,
  parser: Route,
  api: Code,
  http: Globe,
  chatter: MessageSquare,
  reporter: Database,
  memory: BookOpen,
  graph: Network,
  report_writer: FileDown,
  // Container-CC trace: router decision, thinking, and tool calls
  router: Route,
  thinking: Brain,
  Bash: Terminal,
  Read: FileText,
  Write: FileText,
  Edit: FileText,
  MultiEdit: FileText,
  Grep: Search,
  Glob: Search,
};

interface ProcessingStepperProps {
  steps: Step[];
}

export function ProcessingStepper({ steps }: ProcessingStepperProps) {
  const [expanded, setExpanded] = useState(true);

  if (steps.length === 0) return null;

  const activeStep = steps.find((s) => s.status === "active");
  const completedCount = steps.filter((s) => s.status === "complete").length;

  return (
    <div data-testid="stepper" className="border-b bg-muted/20">
      {/* Collapsed summary bar */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 px-4 py-2 text-sm text-muted-foreground transition-colors hover:bg-muted/40"
      >
        <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
        <span className="flex-1 text-left font-medium">
          {activeStep ? activeStep.label : "Processing"}...
          {activeStep?.detail && (
            <span className="ml-1.5 text-xs font-normal opacity-70">
              · {activeStep.detail}
            </span>
          )}
          <span className="ml-1.5 text-xs opacity-60">
            ({completedCount}/{steps.length})
          </span>
        </span>
        <ChevronDown
          className={cn(
            "h-3.5 w-3.5 transition-transform duration-200",
            expanded && "rotate-180",
          )}
        />
      </button>

      {/* Expanded step list */}
      {expanded && (
        <div className="border-t border-border/50 px-4 py-2">
          <div className="space-y-1">
            {steps.map((step) => {
              const Icon = AGENT_ICONS[step.agentName] ?? Globe;

              return (
                <div
                  key={step.index}
                  data-testid={`step-${step.agentName}`}
                  data-status={step.status}
                  className={cn(
                    "flex items-start gap-2 rounded-md px-2 py-1 text-sm",
                    step.status === "pending" && "text-muted-foreground/50",
                    step.status === "active" && "bg-primary/5 text-primary font-medium",
                    step.status === "complete" && "text-muted-foreground",
                    step.status === "error" && "text-destructive",
                  )}
                >
                  {step.status === "active" ? (
                    <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin" />
                  ) : step.status === "complete" ? (
                    <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-green-600" />
                  ) : step.status === "error" ? (
                    <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  ) : (
                    <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  )}
                  <div className="min-w-0 flex-1">
                    <span>{step.label}</span>
                    {step.detail && (
                      <div
                        className={cn(
                          "mt-0.5 truncate font-mono text-xs",
                          step.status === "active"
                            ? "text-primary/70"
                            : "text-muted-foreground/70",
                        )}
                        title={step.detail}
                      >
                        {step.detail}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
