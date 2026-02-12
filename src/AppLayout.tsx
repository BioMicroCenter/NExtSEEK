import { useState, useCallback, useEffect } from "react";
import { useMessages, useProcessingState, useChatApi } from "@/hooks";
import { ChatPanel } from "@/components/ChatPanel";
import { HeaderBar, LeftSidebar, RightSidebar } from "@/components/Layout";
import type {
  ProgressEvent,
  AgentStartedData,
  AgentCompleteData,
  QueryCompleteData,
  QueryErrorData,
  TestCase,
} from "@/lib/types/api";
import type { DebugData } from "@/lib/types/chat";

interface AppLayoutProps {
  credentialError: string | null;
}

export function AppLayout({ credentialError }: AppLayoutProps) {
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);
  const [testCases, setTestCases] = useState<TestCase[]>([]);
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
    fetchTestCases,
    downloadBundle,
  } = useChatApi();

  // Show credential error as system message
  useEffect(() => {
    if (credentialError) {
      addSystemMessage(credentialError);
    }
  }, [credentialError, addSystemMessage]);

  // Fetch test cases on mount
  useEffect(() => {
    if (!credentialError) {
      fetchTestCases().then(setTestCases).catch(() => {});
    }
  }, [credentialError, fetchTestCases]);

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
    (text: string) => {
      addUserMessage(text);
      setDebugData({ entries: [], bundleId: null, query: text });
      submitQuery(text, handleProgress, handleQueryError);
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
        onLeftToggle={() => setLeftOpen(!leftOpen)}
        onRightToggle={() => setRightOpen(!rightOpen)}
        isLeftOpen={leftOpen}
        isRightOpen={rightOpen}
      />
      <div className="flex flex-1 overflow-hidden">
        <ChatPanel
          messages={messages}
          processingState={processingState}
          isDisabled={isDisabled}
          onSendMessage={handleSendMessage}
        />
      </div>
      <LeftSidebar
        isOpen={leftOpen}
        onOpenChange={setLeftOpen}
        testCases={testCases}
        onRunTest={(tc) => handleSendMessage(tc.prompt)}
        onRunAllTests={() => {
          testCases.forEach((tc, i) => {
            setTimeout(() => handleSendMessage(tc.prompt), i * 500);
          });
        }}
      />
      <RightSidebar
        isOpen={rightOpen}
        onOpenChange={setRightOpen}
        debugData={debugData}
        onDownload={handleDownload}
      />
    </div>
  );
}
