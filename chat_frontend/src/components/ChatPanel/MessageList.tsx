import type { Message } from "@/lib/types/chat";
import { MessageBubble } from "./MessageBubble";

interface MessageListProps {
  messages: Message[];
  scrollRef: React.RefObject<HTMLDivElement | null>;
  onDownload?: (bundleId: number) => void;
}

export function MessageList({ messages, scrollRef, onDownload }: MessageListProps) {
  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4">
      {messages.length === 0 ? (
        <div className="flex h-full items-center justify-center">
          <p className="text-lg text-muted-foreground">
            Ask NExtSEEK a question to get started
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {messages.map((msg) => (
            <MessageBubble key={msg.id} message={msg} onDownload={onDownload} />
          ))}
        </div>
      )}
    </div>
  );
}
