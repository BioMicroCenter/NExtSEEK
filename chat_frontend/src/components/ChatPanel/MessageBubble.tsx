import { cn } from "@/lib/utils";
import type { Message } from "@/lib/types/chat";
import { MarkdownContent } from "./MarkdownContent";

interface MessageBubbleProps {
  message: Message;
}

export function MessageBubble({ message }: MessageBubbleProps) {
  if (message.messageType === "system") {
    return (
      <div className="flex justify-center py-1">
        <p className="text-base italic text-muted-foreground">{message.content}</p>
      </div>
    );
  }

  return (
    <div
      className={cn("flex py-1", message.isUser ? "justify-end" : "justify-start")}
    >
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
    </div>
  );
}
