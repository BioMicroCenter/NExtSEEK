import { useState, useMemo, useId } from "react";
import { ChevronDown, CornerDownRight, Info, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Message } from "@/lib/types/chat";
import { Badge } from "@/components/ui/badge";
import { MarkdownContent } from "./MarkdownContent";
import { ReportArtifacts } from "./ReportArtifacts";
import { CCActivityPanel } from "./CCActivityPanel";

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

  const debugPattern =
    /(?:^|\n)(#{1,3}\s+Debug Info|\*\*Debug Info\*\*)\s*\n```[\s\S]*?```/gi;
  clean = clean.replace(debugPattern, (match) => {
    extracted.push(match.trim());
    return ""
  })

  // Clean up excess blank lines left behind
  clean = clean.replace(/\n{3,}/g, "\n\n").trim();

  return { cleanContent: clean, extractedSections: extracted };
}

interface MessageBubbleProps {
  message: Message;
  index?: number;
  onArtifactDownload?: (bundleId: number, artifactKey: string) => void;
  onCcArtifactDownload?: (artifactKey: string) => void;
  /** Sends a suggestion chip's query as the next message. */
  onSuggestion?: (query: string) => void;
  /** A turn is in flight: the chips show but cannot be clicked. */
  disabled?: boolean;
  /** This is the newest assistant reply, the only one whose chips show. */
  isLast?: boolean;
}

export function MessageBubble({
  message,
  index,
  onArtifactDownload,
  onCcArtifactDownload,
  onSuggestion,
  disabled,
  isLast,
}: MessageBubbleProps) {
  const [debugOpen, setDebugOpen] = useState(false);
  const [reasonOpen, setReasonOpen] = useState<number | null>(null);
  const detailsId = useId();

  // Strip debug sections from assistant messages
  const { cleanContent, extractedSections } = useMemo(() => {
    if (message.isUser || message.messageType === "system") {
      return { cleanContent: message.content, extractedSections: [] };
    }
    return stripDebugSections(message.content);
  }, [message.content, message.isUser, message.messageType]);

  const handleDl = (key: string) =>
    message.mode === "cc"
      ? onCcArtifactDownload?.(key)
      : onArtifactDownload?.(message.bundleId!, key);

  if (message.messageType === "system") {
    // An error can carry files: a Container-CC turn stopped at its time limit
    // still publishes what it wrote. They sit under the error line, with the
    // same download buttons a completed turn's get.
    const hasArtifacts = (message.artifacts?.length ?? 0) > 0;
    return (
      <div className={cn("flex py-1", hasArtifacts ? "flex-col items-center" : "justify-center")}>
        <p className="text-base italic text-muted-foreground">{message.content}</p>
        {hasArtifacts && (
          <ReportArtifacts artifacts={message.artifacts!} onDownloadArtifact={handleDl} />
        )}
      </div>
    );
  }

  const hasDebug = !message.isUser && message.debugEntries && message.debugEntries.length > 0;
  const hasExtracted = extractedSections.length > 0;
  const hasCcTrace = !message.isUser && (message.ccTraces?.length ?? 0) > 0;
  const hasSearchDetails = hasDebug || hasExtracted || hasCcTrace;
  // The reviewer's chips (#128): under the newest reply only, and only those with
  // a label to show and a query to send, since the list comes from the server.
  const chips =
    !message.isUser && isLast && onSuggestion && Array.isArray(message.suggestions)
      ? message.suggestions.filter(
          (s) => typeof s?.label === "string" && s.label !== "" && typeof s.query === "string" && s.query !== "",
        )
      : [];

  return (
    <div
      data-testid="message-bubble"
      data-role={message.isUser ? "user" : "assistant"}
      data-bubble-index={index ?? 0}
      className={cn(
        "flex flex-col py-1",
        message.isUser ? "items-end" : "items-start",
      )}
    >
      {/* Message bubble */}
      <div
        className={cn(
          "px-4 py-2 text-lg md:max-w-[80%]",
          message.isUser ? "max-w-[85%]" : "max-w-full",
          message.isUser
            ? "whitespace-pre-wrap rounded-2xl rounded-br-sm bg-primary text-primary-foreground"
            : "rounded-2xl rounded-bl-sm border bg-card",
        )}
      >
        {message.isUser ? message.content : <MarkdownContent content={cleanContent} />}

        {/* Inline report tables */}
        {!message.isUser && message.artifacts && message.artifacts.length > 0 && (
          <ReportArtifacts
            artifacts={message.artifacts}
            onDownloadArtifact={handleDl}
          />
        )}
      </div>

      {/* Suggested next questions. Label and reason are React text, never markup. */}
      {chips.length > 0 && (
        <div
          role="group"
          aria-label="Suggested next questions"
          className="mt-1.5 flex max-w-full flex-wrap gap-1.5 md:max-w-[80%]"
        >
          {chips.map((s, i) => (
            <div key={s.id || i} className="flex flex-col">
              <div className="flex items-stretch gap-0.5">
                <button
                  type="button"
                  data-testid="suggestion-chip"
                  data-source={s.source}
                  data-suggestion-id={s.id}
                  disabled={disabled}
                  onClick={() => onSuggestion?.(s.query)}
                  className="flex items-center gap-1 rounded-md border border-border/60 px-2 py-1 text-left text-xs text-muted-foreground transition-colors enabled:hover:bg-muted/60 enabled:hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50 [@media(pointer:coarse)]:min-h-11"
                >
                  <CornerDownRight className="h-3 w-3 shrink-0" />
                  <span>{s.label}</span>
                </button>
                {s.reason && (
                  <button
                    type="button"
                    data-testid="suggestion-reason-toggle"
                    aria-label={`Why: ${s.label}`}
                    aria-expanded={reasonOpen === i}
                    aria-controls={`${detailsId}-reason-${i}`}
                    onClick={() => setReasonOpen((v) => (v === i ? null : i))}
                    className="flex items-center rounded-md px-1.5 text-muted-foreground hover:bg-muted/60 hover:text-foreground [@media(pointer:coarse)]:min-h-11 [@media(pointer:coarse)]:min-w-11 [@media(pointer:coarse)]:justify-center"
                  >
                    <Info className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
              {s.reason && reasonOpen === i && (
                <p id={`${detailsId}-reason-${i}`} className="mt-1 max-w-xs text-xs text-muted-foreground">
                  {s.reason}
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Search Details (assistant messages only) */}
      {hasSearchDetails && (
        <div className="mt-1.5 flex max-w-full flex-col gap-1.5 md:max-w-[80%]">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setDebugOpen((v) => !v)}
              aria-expanded={debugOpen}
              aria-controls={detailsId}
              className="flex items-center gap-1 rounded-md px-2 py-1 text-xs [@media(pointer:coarse)]:min-h-11 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
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
          </div>

          {/* Collapsible details panel */}
          {debugOpen && hasSearchDetails && (
            <div id={detailsId} className="rounded-lg border border-border/60 bg-muted/20 p-3">
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
                      <Badge variant="secondary" className="shrink-0 text-xs font-mono">
                        {entry.agent}
                      </Badge>
                      <p className="text-xs leading-relaxed text-muted-foreground">
                        {entry.summary}
                      </p>
                    </div>
                  ))}
                </div>
              )}

              {hasCcTrace && (
                <div className={cn(hasDebug || hasExtracted ? "mt-2 border-t border-border/40 pt-2" : "")}>
                  {message.ccTraces!.map((trace, i) => (
                    <CCActivityPanel key={`${trace.cc_session_id}-${i}`} trace={trace} />
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
