import { useState, useMemo } from "react";
import { ChevronDown, Download, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Message } from "@/lib/types/chat";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MarkdownContent } from "./MarkdownContent";

/**
 * Strip "NExtSEEK search summary" and "API request preview" sections from
 * the LLM reply markdown. Returns the cleaned content and extracted sections
 * to display inside the collapsible Search Details panel.
 */
function stripDebugSections(content: string): {
  cleanContent: string;
  extractedSections: string[];
} {
  const extracted: string[] = [];
  let clean = content;

  // Pattern: "## NExtSEEK search summary" or "**NExtSEEK search summary**"
  // followed by bullet list, up to the next heading or double newline before non-list content
  const summaryPattern =
    /(?:^|\n)(#{1,3}\s+NExtSEEK search summary|\*\*NExtSEEK search summary\*\*)\s*\n([\s\S]*?)(?=\n#{1,3}\s|\n\*\*[A-Z]|\n\n(?![*\-•])(?!\s)|$)/gi;
  clean = clean.replace(summaryPattern, (match) => {
    extracted.push(match.trim());
    return "";
  });

  // Pattern: "## API request preview" or "**API request preview**"
  // followed by a fenced code block
  const previewPattern =
    /(?:^|\n)(#{1,3}\s+API request preview|\*\*API request preview\*\*)\s*\n```[\s\S]*?```/gi;
  clean = clean.replace(previewPattern, (match) => {
    extracted.push(match.trim());
    return "";
  });

  // Clean up excess blank lines left behind
  clean = clean.replace(/\n{3,}/g, "\n\n").trim();

  return { cleanContent: clean, extractedSections: extracted };
}

interface MessageBubbleProps {
  message: Message;
  onDownload?: (bundleId: number) => void;
}

export function MessageBubble({ message, onDownload }: MessageBubbleProps) {
  const [debugOpen, setDebugOpen] = useState(false);

  // Strip debug sections from assistant messages
  const { cleanContent, extractedSections } = useMemo(() => {
    if (message.isUser || message.messageType === "system") {
      return { cleanContent: message.content, extractedSections: [] };
    }
    return stripDebugSections(message.content);
  }, [message.content, message.isUser, message.messageType]);

  if (message.messageType === "system") {
    return (
      <div className="flex justify-center py-1">
        <p className="text-base italic text-muted-foreground">{message.content}</p>
      </div>
    );
  }

  const hasDebug = !message.isUser && message.debugEntries && message.debugEntries.length > 0;
  const hasExtracted = extractedSections.length > 0;
  const hasSearchDetails = hasDebug || hasExtracted;
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
        {message.isUser ? message.content : <MarkdownContent content={cleanContent} />}
      </div>

      {/* Search Details + Download (assistant messages only) */}
      {(hasSearchDetails || hasBundleId) && (
        <div className="mt-1.5 flex max-w-[80%] flex-col gap-1.5">
          {/* Toggle row: Search Details + Download Results side by side */}
          <div className="flex items-center gap-2">
            {hasSearchDetails && (
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
            )}

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

          {/* Collapsible details panel */}
          {debugOpen && hasSearchDetails && (
            <div className="rounded-lg border border-border/60 bg-muted/20 p-3">
              {/* Extracted markdown sections (search summary, API preview) */}
              {extractedSections.map((section, i) => (
                <div key={i} className="mb-2 text-xs last:mb-0">
                  <MarkdownContent content={section} />
                </div>
              ))}

              {/* Agent debug entries */}
              {hasDebug && (
                <div className={cn("space-y-2", hasExtracted && "mt-2 border-t border-border/40 pt-2")}>
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
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
