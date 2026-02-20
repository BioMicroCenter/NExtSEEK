import {
  Brain,
  Route,
  Code,
  Globe,
  MessageSquare,
  Database,
  BookOpen,
  Check,
  Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { Step } from "@/lib/types/chat";

const AGENT_ICONS: Record<string, React.ElementType> = {
  entity: Brain,
  parser: Route,
  api: Code,
  http: Globe,
  chatter: MessageSquare,
  reporter: Database,
  memory: BookOpen,
};

interface ProcessingStepperProps {
  steps: Step[];
}

export function ProcessingStepper({ steps }: ProcessingStepperProps) {
  if (steps.length === 0) return null;

  return (
    <div className="border-t bg-muted/30 px-4 py-3">
      <div className="flex items-center justify-center gap-1">
        {steps.map((step, i) => {
          const Icon = AGENT_ICONS[step.agentName] ?? Globe;

          return (
            <div key={step.agentName} className="flex items-center">
              {i > 0 && (
                <div
                  className={cn(
                    "mx-1 h-px w-6",
                    step.status === "pending"
                      ? "bg-muted-foreground/30"
                      : "bg-primary/50",
                  )}
                />
              )}
              <div
                className={cn(
                  "flex items-center gap-1.5 rounded-full px-2.5 py-1 text-sm transition-colors",
                  step.status === "pending" &&
                    "text-muted-foreground/50",
                  step.status === "active" &&
                    "bg-primary/10 text-primary font-medium",
                  step.status === "complete" &&
                    "text-green-600 dark:text-green-400",
                  step.status === "error" &&
                    "text-destructive",
                )}
                title={step.label}
              >
                {step.status === "active" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : step.status === "complete" ? (
                  <Check className="h-4 w-4" />
                ) : (
                  <Icon className="h-4 w-4" />
                )}
                <span className="hidden sm:inline">{step.label}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
