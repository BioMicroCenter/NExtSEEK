import { useRef, useCallback } from "react";

const MIN_HEIGHT = 40;
const MAX_HEIGHT = 200;

interface UseAutoResizeReturn {
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  handleInput: () => void;
  resetHeight: () => void;
}

export function useAutoResize(): UseAutoResizeReturn {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const handleInput = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    textarea.style.height = "auto";
    const newHeight = Math.min(
      Math.max(textarea.scrollHeight, MIN_HEIGHT),
      MAX_HEIGHT,
    );
    textarea.style.height = `${newHeight}px`;
  }, []);

  const resetHeight = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = `${MIN_HEIGHT}px`;
  }, []);

  return { textareaRef, handleInput, resetHeight };
}
