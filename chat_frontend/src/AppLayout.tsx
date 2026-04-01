import { useState, useCallback, useEffect } from "react";
import { useMessages, useProcessingState, useChatApi } from "@/hooks";
import { ChatPanel } from "@/components/ChatPanel";
import { HeaderBar, RightSidebar } from "@/components/Layout";
import type {
  ProgressEvent,
  AgentStartedData,
  AgentCompleteData,
  QueryCompleteData,
  QueryErrorData,
} from "@/lib/types/api";
import type { DebugData } from "@/lib/types/chat";

interface AppLayoutProps {
  credentialError: string | null;
}

export function AppLayout({ credentialError }: AppLayoutProps) {
  const [rightOpen, setRightOpen] = useState(false);
  const [debugData, setDebugData] = useState<DebugData>({
    entries: [],
    bundleId: null,
    query: "",
  });

  const { messages, addUserMessage, addAssistantMessage, addSystemMessage } =
    useMessages();
  const {
    processingState,
    handleAgentStarted,
    handleAgentComplete,
    resetProcessing,
  } = useProcessingState();

  const {
    isQuerying,
    sessionId,
    submitQuery,
    downloadBundle,
  } = useChatApi();

  // Show credential error as system message
  useEffect(() => {
    if (credentialError) {
      addSystemMessage(credentialError);
    }
  }, [credentialError, addSystemMessage]);

  const handleProgress = useCallback(
    (event: ProgressEvent) => {
      switch (event.event) {
        case "agent_started": {
          const d = event.data as AgentStartedData;
          handleAgentStarted(d.agent, d.mode);
          break;
        }
        case "agent_complete": {
          const d = event.data as AgentCompleteData;
          handleAgentComplete(d.agent);
          setDebugData((prev) => ({
            ...prev,
            entries: [
              ...prev.entries,
              { agent: d.agent, summary: typeof d.summary === "string" ? d.summary : JSON.stringify(d.summary ?? "", null, 2), timestamp: new Date() },
            ],
          }));
          break;
        }
        case "query_complete": {
          const d = event.data as QueryCompleteData;
          addAssistantMessage(d.reply);
          resetProcessing();
          setDebugData((prev) => ({
            ...prev,
            bundleId: d.bundle_id,
          }));
          break;
        }
        case "query_error": {
          const d = event.data as QueryErrorData;
          addSystemMessage(`Error: ${d.error}`);
          resetProcessing();
          break;
        }
      }
    },
    [handleAgentStarted, handleAgentComplete, addAssistantMessage, addSystemMessage, resetProcessing],
  );

  const handleQueryError = useCallback(
    (error: string) => {
      addSystemMessage(`Error: ${error}`);
      resetProcessing();
    },
    [addSystemMessage, resetProcessing],
  );

  const handleSendMessage = useCallback(
    (text: string, mode: string) => {
      addUserMessage(text);
      setDebugData({ entries: [], bundleId: null, query: text });
      submitQuery(text, mode, handleProgress, handleQueryError);
    },
    [addUserMessage, submitQuery, handleProgress, handleQueryError],
  );

  const handleDownload = useCallback(
    (format: string) => {
      if (sessionId && debugData.bundleId) {
        downloadBundle(sessionId, debugData.bundleId, format);
      }
    },
    [sessionId, debugData.bundleId, downloadBundle],
  );

  const isDisabled = !!credentialError || isQuerying;

  return (
    <div className="flex h-screen flex-col bg-background text-foreground">
      <HeaderBar
        onRightToggle={() => setRightOpen(!rightOpen)}
      />
      <div className="flex flex-1 overflow-hidden">
        <ChatPanel
          messages={messages}
          processingState={processingState}
          isDisabled={isDisabled}
          onSendMessage={handleSendMessage}
        />
      </div>
      <RightSidebar
        isOpen={rightOpen}
        onOpenChange={setRightOpen}
        debugData={debugData}
        onDownload={handleDownload}
      />
    </div>
  );
}
