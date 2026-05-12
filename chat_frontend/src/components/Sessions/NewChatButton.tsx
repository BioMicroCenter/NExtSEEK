import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";

interface NewChatButtonProps {
  collapsed: boolean;
  disabled?: boolean;
  onClick: () => void;
}

export function NewChatButton({ collapsed, disabled, onClick }: NewChatButtonProps) {
  return (
    <Button
      variant="outline"
      size={collapsed ? "icon" : "default"}
      onClick={onClick}
      disabled={disabled}
      className={collapsed ? "w-10" : "w-full justify-start"}
      aria-label="New chat"
      title="New chat"
    >
      <Plus className="h-4 w-4" />
      {!collapsed && <span className="ml-2">New chat</span>}
    </Button>
  );
}
