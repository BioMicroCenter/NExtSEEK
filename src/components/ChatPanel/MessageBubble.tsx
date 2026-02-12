import { cn } from "@/lib/utils";
import type { Message } from "@/lib/types/chat";

interface MessageBubbleProps {
  message: Message;
}

export function MessageBubble({ message }: MessageBubbleProps) {
  if (message.messageType === "system") {
    return (
      <div className="flex justify-center py-1">
        <p className="text-sm italic text-muted-foreground">{message.content}</p>
      </div>
    );
  }

  return (
    <div
      className={cn("flex py-1", message.isUser ? "justify-end" : "justify-start")}
    >
      <div
        className={cn(
          "max-w-[80%] whitespace-pre-wrap px-4 py-2 text-sm",
          message.isUser
            ? "rounded-2xl rounded-br-sm bg-primary text-primary-foreground"
            : "rounded-2xl rounded-bl-sm border bg-card",
        )}
      >
        {message.content}
      </div>
    </div>
  );
}
