import type { Message, ProcessingState } from "@/lib/types/chat";
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
  isAdmin?: boolean;
}

export function ChatPanel({
  messages,
  processingState,
  isDisabled,
  onSendMessage,
  onArtifactDownload,
  isAdmin = false,
}: ChatPanelProps) {
  const { scrollRef } = useAutoScroll([messages, processingState.steps]);

  return (
    <div data-testid="chat-panel" className="flex flex-1 flex-col overflow-hidden">
      {processingState.isProcessing && (
        <ProcessingStepper steps={processingState.steps} />
      )}
      <MessageList messages={messages} scrollRef={scrollRef} onArtifactDownload={onArtifactDownload} />
      <MessageInput
        onSend={onSendMessage}
        disabled={isDisabled}
        isAdmin={isAdmin}
      />
    </div>
  );
}
