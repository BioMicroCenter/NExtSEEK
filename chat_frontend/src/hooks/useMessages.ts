import { useState, useCallback, useRef } from "react";
import type { Message } from "@/lib/types/chat";

interface UseMessagesReturn {
  messages: Message[];
  addUserMessage: (content: string) => void;
  addAssistantMessage: (content: string) => void;
  addSystemMessage: (content: string) => void;
  updateLastAssistantMessage: (patch: Partial<Message>) => void;
  clearMessages: () => void;
}

function createMessage(
  id: string,
  content: string,
  isUser: boolean,
  messageType: Message["messageType"] = "text",
): Message {
  return {
    id,
    content,
    isUser,
    timestamp: new Date(),
    status: "sent",
    messageType,
  };
}

export function useMessages(): UseMessagesReturn {
  const [messages, setMessages] = useState<Message[]>([]);
  const counterRef = useRef(0);

  const nextId = () => `msg-${Date.now()}-${++counterRef.current}`;

  const addUserMessage = useCallback((content: string) => {
    setMessages((prev) => [...prev, createMessage(nextId(), content, true, "text")]);
  }, []);

  const addAssistantMessage = useCallback((content: string) => {
    setMessages((prev) => [...prev, createMessage(nextId(), content, false, "text")]);
  }, []);

  const addSystemMessage = useCallback((content: string) => {
    setMessages((prev) => [...prev, createMessage(nextId(), content, false, "system")]);
  }, []);

  const updateLastAssistantMessage = useCallback((patch: Partial<Message>) => {
    setMessages((prev) => {
      for (let i = prev.length - 1; i >= 0; i--) {
        if (!prev[i].isUser && prev[i].messageType === "text") {
          const updated = [...prev];
          updated[i] = { ...updated[i], ...patch };
          return updated;
        }
      }
      return prev;
    });
  }, []);

  const clearMessages = useCallback(() => {
    setMessages([]);
  }, []);

  return { messages, addUserMessage, addAssistantMessage, addSystemMessage, updateLastAssistantMessage, clearMessages };
}
