import type { Message } from "@/lib/types/chat";
import { MessageBubble } from "./MessageBubble";

interface MessageListProps {
  messages: Message[];
  scrollRef: React.RefObject<HTMLDivElement | null>;
  onArtifactDownload?: (bundleId: number, artifactKey: string) => void;
  onCcArtifactDownload?: (artifactKey: string) => void;
  onSuggestion?: (query: string) => void;
  disabled?: boolean;
}

/** The newest assistant reply: the message a turn's `query_complete` patches. */
function lastAssistantIndex(messages: Message[]): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (!messages[i].isUser && messages[i].messageType === "text") return i;
  }
  return -1;
}

export function MessageList({
  messages,
  scrollRef,
  onArtifactDownload,
  onCcArtifactDownload,
  onSuggestion,
  disabled,
}: MessageListProps) {
  // Chips show under the newest reply only, so an older reply's chips go once a newer reply arrives.
  const lastAssistant = lastAssistantIndex(messages);
  return (
    <div ref={scrollRef} data-testid="message-list" className="flex-1 overflow-y-auto px-4 py-4">
      {messages.length === 0 ? (
        <div className="flex h-full items-center justify-center">
          <p className="text-lg text-muted-foreground">
            Ask NExtSEEK a question to get started
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {messages.map((msg, idx) => (
            <MessageBubble
              key={msg.id}
              message={msg}
              index={idx}
              onArtifactDownload={onArtifactDownload}
              onCcArtifactDownload={onCcArtifactDownload}
              onSuggestion={onSuggestion}
              disabled={disabled}
              isLast={idx === lastAssistant}
            />
          ))}
        </div>
      )}
    </div>
  );
}
