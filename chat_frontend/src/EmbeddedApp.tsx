import { useState, useCallback, useEffect, useRef } from "react";
import { useMessages, useProcessingState } from "@/hooks";
import { NextseekApiService } from "@/lib/services/chatApi";
import { SessionAuthService } from "@/lib/services/sessionAuth";
import { ChatPanel } from "@/components/ChatPanel";
import { CompactToolbar, LeftSidebar } from "@/components/Layout";
import type {
  ProgressEvent,
  AgentStartedData,
  AgentCompleteData,
  QueryCompleteData,
  QueryErrorData,
  TestCase,
} from "@/lib/types/api";

export function EmbeddedApp() {
  const [leftOpen, setLeftOpen] = useState(false);
  const [testCases, setTestCases] = useState<TestCase[]>([]);

  const serviceRef = useRef(
    new NextseekApiService(new SessionAuthService()),
  );
  const [isQuerying, setIsQuerying] = useState(false);

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
          break;
        }
        case "query_complete": {
          const d = event.data as QueryCompleteData;
          addAssistantMessage(d.reply);
          resetProcessing();
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
      setIsQuerying(true);

      serviceRef.current
        .submitQuery(text, handleProgress, handleQueryError)
        .finally(() => {
          setIsQuerying(false);
        });
    },
    [addUserMessage, handleProgress, handleQueryError],
  );

  return (
    <div className="flex h-full flex-col bg-background text-foreground">
      <CompactToolbar
        onLeftToggle={() => setLeftOpen(!leftOpen)}
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
    </div>
  );
}
