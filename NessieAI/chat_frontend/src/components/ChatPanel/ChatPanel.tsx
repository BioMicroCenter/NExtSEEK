import { useCallback } from "react";
import type { Message, ProcessingState } from "@/lib/types/chat";
import type { NextseekApiService } from "@/lib/services/chatApi";
import { useAutoScroll } from "@/hooks/useAutoScroll";
import { MessageList } from "./MessageList";
import { MessageInput, type SendOptions } from "./MessageInput";
import { ProcessingStepper } from "./ProcessingStepper";

interface ChatPanelProps {
  messages: Message[];
  processingState: ProcessingState;
  isDisabled: boolean;
  onSendMessage: (message: string, opts: SendOptions) => void;
  onArtifactDownload?: (bundleId: number, artifactKey: string) => void;
  onCcArtifactDownload?: (artifactKey: string) => void;
  apiService?: NextseekApiService;
}

/** What a screen reader hears when the newest message is not the user's own. */
function announcement(messages: Message[]): string {
  const last = messages[messages.length - 1];
  if (!last || last.isUser) return "";
  return last.messageType === "system" ? `Notice: ${last.content}` : "Answer ready";
}

export function ChatPanel({
  messages,
  processingState,
  isDisabled,
  onSendMessage,
  onArtifactDownload,
  onCcArtifactDownload,
  apiService,
}: ChatPanelProps) {
  const { scrollRef } = useAutoScroll([messages, processingState.steps]);
  // A suggestion chip sends its query exactly as the composer sends a typed
  // message: same handler, same options, no route of its own.
  const handleSuggestion = useCallback(
    (query: string) => onSendMessage(query, { pipeline: "standard" }),
    [onSendMessage],
  );

  return (
    <div data-testid="chat-panel" className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      {processingState.isProcessing && (
        <ProcessingStepper steps={processingState.steps} />
      )}
      <div role="status" className="sr-only" data-testid="chat-status">
        {announcement(messages)}
      </div>
      <MessageList
        messages={messages}
        scrollRef={scrollRef}
        onArtifactDownload={onArtifactDownload}
        onCcArtifactDownload={onCcArtifactDownload}
        onSuggestion={handleSuggestion}
        disabled={isDisabled}
      />
      <MessageInput
        onSend={onSendMessage}
        disabled={isDisabled}
        apiService={apiService}
      />
    </div>
  );
}
