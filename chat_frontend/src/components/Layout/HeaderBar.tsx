import { useEffect, useState } from "react";
import { Database, Moon, PanelLeftOpen, PanelRightOpen, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";

interface HeaderBarProps {
  onLeftToggle: () => void;
  onRightToggle: () => void;
  isLeftOpen: boolean;
  isRightOpen: boolean;
}

export function HeaderBar({
  onLeftToggle,
  onRightToggle,
}: HeaderBarProps) {
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem("theme");
    if (stored === "dark" || (!stored && window.matchMedia("(prefers-color-scheme: dark)").matches)) {
      document.documentElement.classList.add("dark");
      setIsDark(true);
    }
  }, []);

  const toggleTheme = () => {
    const next = !isDark;
    setIsDark(next);
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("theme", next ? "dark" : "light");
  };

  return (
    <header className="flex h-12 shrink-0 items-center border-b bg-background px-4">
      <Button
        variant="ghost"
        size="sm"
        onClick={onLeftToggle}
        aria-label="Toggle tests panel"
      >
        <PanelLeftOpen className="h-4 w-4" />
        <span className="ml-1 hidden sm:inline text-xs">Tests</span>
      </Button>

      <div className="flex flex-1 items-center justify-center gap-2">
        <Database className="h-4 w-4 text-primary" />
        <span className="text-sm font-semibold">NExtSEEK Chat</span>
      </div>

      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleTheme}
          aria-label="Toggle dark mode"
        >
          {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={onRightToggle}
          aria-label="Toggle debug panel"
        >
          <span className="mr-1 hidden sm:inline text-xs">Debug</span>
          <PanelRightOpen className="h-4 w-4" />
        </Button>
      </div>
    </header>
  );
}
