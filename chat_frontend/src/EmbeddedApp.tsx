import { useState, useCallback, useEffect, useRef } from "react";
import { useMessages, useProcessingState } from "@/hooks";
import { NextseekApiService } from "@/lib/services/chatApi";
import { SessionAuthService } from "@/lib/services/sessionAuth";
import { ChatPanel } from "@/components/ChatPanel";
import { CompactToolbar, LeftSidebar, RightSidebar } from "@/components/Layout";
import type {
  ProgressEvent,
  AgentStartedData,
  AgentCompleteData,
  QueryCompleteData,
  QueryErrorData,
  TestCase,
} from "@/lib/types/api";
import type { DebugData } from "@/lib/types/chat";

export function EmbeddedApp() {
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);
  const [testCases, setTestCases] = useState<TestCase[]>([]);
  const [debugData, setDebugData] = useState<DebugData>({
    entries: [],
    bundleId: null,
    query: "",
  });

  const serviceRef = useRef(
    new NextseekApiService(new SessionAuthService()),
  );
  const [isQuerying, setIsQuerying] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);

  const { messages, addUserMessage, addAssistantMessage, addSystemMessage } =
    useMessages();
  const {
    processingState,
    handleAgentStarted,
    handleAgentComplete,
    resetProcessing,
  } = useProcessingState();

  // Fetch test cases on mount
  useEffect(() => {
    serviceRef.current.fetchTestCases().then(setTestCases).catch(() => {});
  }, []);

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
              {
                agent: d.agent,
                summary:
                  typeof d.summary === "string"
                    ? d.summary
                    : JSON.stringify(d.summary ?? "", null, 2),
                timestamp: new Date(),
              },
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
      setIsQuerying(true);

      serviceRef.current
        .submitQuery(text, handleProgress, handleQueryError)
        .finally(() => {
          setSessionId(serviceRef.current.sessionId);
          setIsQuerying(false);
        });
    },
    [addUserMessage, handleProgress, handleQueryError],
  );

  const handleDownload = useCallback(
    (format: string) => {
      if (sessionId && debugData.bundleId) {
        serviceRef.current.downloadBundle(sessionId, debugData.bundleId, format);
      }
    },
    [sessionId, debugData.bundleId],
  );

  return (
    <div className="flex h-full flex-col bg-background text-foreground">
      <CompactToolbar
        onLeftToggle={() => setLeftOpen(!leftOpen)}
        onRightToggle={() => setRightOpen(!rightOpen)}
      />
      <div className="flex flex-1 overflow-hidden">
        <ChatPanel
          messages={messages}
          processingState={processingState}
          isDisabled={isQuerying}
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
