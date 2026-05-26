import type { Message } from "@/lib/types/chat";
import { MessageBubble } from "./MessageBubble";

interface MessageListProps {
  messages: Message[];
  scrollRef: React.RefObject<HTMLDivElement | null>;
  onArtifactDownload?: (bundleId: number, artifactKey: string) => void;
}

export function MessageList({ messages, scrollRef, onArtifactDownload }: MessageListProps) {
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
            <MessageBubble key={msg.id} message={msg} index={idx} onArtifactDownload={onArtifactDownload} />
          ))}
        </div>
      )}
    </div>
  );
}
