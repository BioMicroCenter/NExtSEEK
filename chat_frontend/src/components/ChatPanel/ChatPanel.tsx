import type { Message, ProcessingState } from "@/lib/types/chat";
import { useAutoScroll } from "@/hooks/useAutoScroll";
import { MessageList } from "./MessageList";
import { MessageInput } from "./MessageInput";
import { ProcessingStepper } from "./ProcessingStepper";

interface ChatPanelProps {
  messages: Message[];
  processingState: ProcessingState;
  isDisabled: boolean;
  onSendMessage: (message: string) => void;
}

export function ChatPanel({
  messages,
  processingState,
  isDisabled,
  onSendMessage,
}: ChatPanelProps) {
  const { scrollRef } = useAutoScroll([messages, processingState.steps]);

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <MessageList messages={messages} scrollRef={scrollRef} />
      {processingState.isProcessing && (
        <ProcessingStepper steps={processingState.steps} />
      )}
      <MessageInput
        onSend={onSendMessage}
        disabled={isDisabled}
      />
    </div>
  );
}
