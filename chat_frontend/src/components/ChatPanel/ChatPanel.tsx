import type { Message, ProcessingState } from "@/lib/types/chat";
import { useAutoScroll } from "@/hooks/useAutoScroll";
import { MessageList } from "./MessageList";
import { MessageInput } from "./MessageInput";
import { ProcessingStepper } from "./ProcessingStepper";

interface ChatPanelProps {
  messages: Message[];
  processingState: ProcessingState;
  isDisabled: boolean;
  onSendMessage: (message: string, mode: string) => void;
  onArtifactDownload?: (bundleId: number, artifactKey: string) => void;
}

export function ChatPanel({
  messages,
  processingState,
  isDisabled,
  onSendMessage,
  onArtifactDownload,
}: ChatPanelProps) {
  const { scrollRef } = useAutoScroll([messages, processingState.steps]);

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {processingState.isProcessing && (
        <ProcessingStepper steps={processingState.steps} />
      )}
      <MessageList messages={messages} scrollRef={scrollRef} onArtifactDownload={onArtifactDownload} />
      <MessageInput
        onSend={onSendMessage}
        disabled={isDisabled}
      />
    </div>
  );
}
