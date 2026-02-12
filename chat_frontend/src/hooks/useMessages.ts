import { useState, useCallback, useRef } from "react";
import type { Message } from "@/lib/types/chat";

interface UseMessagesReturn {
  messages: Message[];
  addUserMessage: (content: string) => void;
  addAssistantMessage: (content: string) => void;
  addSystemMessage: (content: string) => void;
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

  const clearMessages = useCallback(() => {
    setMessages([]);
  }, []);

  return { messages, addUserMessage, addAssistantMessage, addSystemMessage, clearMessages };
}
