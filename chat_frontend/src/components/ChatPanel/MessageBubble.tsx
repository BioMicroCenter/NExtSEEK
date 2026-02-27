import { useState } from "react";
import { ChevronDown, Download, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Message } from "@/lib/types/chat";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MarkdownContent } from "./MarkdownContent";

interface MessageBubbleProps {
  message: Message;
  onDownload?: (bundleId: number) => void;
}

export function MessageBubble({ message, onDownload }: MessageBubbleProps) {
  const [debugOpen, setDebugOpen] = useState(false);

  if (message.messageType === "system") {
    return (
      <div className="flex justify-center py-1">
        <p className="text-base italic text-muted-foreground">{message.content}</p>
      </div>
    );
  }

  const hasDebug = !message.isUser && message.debugEntries && message.debugEntries.length > 0;
  const hasBundleId = !message.isUser && message.bundleId != null;

  return (
    <div
      className={cn(
        "flex flex-col py-1",
        message.isUser ? "items-end" : "items-start",
      )}
    >
      {/* Message bubble */}
      <div
        className={cn(
          "max-w-[80%] px-4 py-2 text-lg",
          message.isUser
            ? "whitespace-pre-wrap rounded-2xl rounded-br-sm bg-primary text-primary-foreground"
            : "rounded-2xl rounded-bl-sm border bg-card",
        )}
      >
        {message.isUser ? message.content : <MarkdownContent content={message.content} />}
      </div>

      {/* Debug details + download (assistant messages only) */}
      {(hasDebug || hasBundleId) && (
        <div className="mt-1.5 flex max-w-[80%] flex-col gap-1.5">
          {/* Search Details toggle */}
          {hasDebug && (
            <div>
              <button
                type="button"
                onClick={() => setDebugOpen((v) => !v)}
                className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
              >
                <Search className="h-3 w-3" />
                <span>Search Details</span>
                <ChevronDown
                  className={cn(
                    "h-3 w-3 transition-transform duration-200",
                    debugOpen && "rotate-180",
                  )}
                />
              </button>

              {debugOpen && (
                <div className="mt-1 rounded-lg border border-border/60 bg-muted/20 p-3">
                  <div className="space-y-2">
                    {message.debugEntries!.map((entry, i) => (
                      <div key={i} className="flex items-start gap-2">
                        <Badge variant="secondary" className="shrink-0 text-[10px] font-mono">
                          {entry.agent}
                        </Badge>
                        <p className="text-xs leading-relaxed text-muted-foreground">
                          {entry.summary}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Download Results button */}
          {hasBundleId && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-fit gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground"
              onClick={() => onDownload?.(message.bundleId!)}
            >
              <Download className="h-3 w-3" />
              Download Results
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
