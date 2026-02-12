import { Play } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { TestCase } from "@/lib/types/api";

interface TestCaseListProps {
  testCases: TestCase[];
  onRunTest: (testCase: TestCase) => void;
}

export function TestCaseList({ testCases, onRunTest }: TestCaseListProps) {
  if (testCases.length === 0) {
    return (
      <p className="px-4 py-2 text-sm text-muted-foreground">
        No test cases available
      </p>
    );
  }

  return (
    <div className="space-y-1">
      {testCases.map((tc) => (
        <div
          key={tc.id}
          className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-muted/50"
        >
          <Badge variant="secondary" className="shrink-0 text-xs">
            {tc.id}
          </Badge>
          <span className="flex-1 truncate text-sm">{tc.prompt}</span>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onRunTest(tc)}
            aria-label={`Run test ${tc.id}`}
          >
            <Play className="h-3 w-3" />
          </Button>
        </div>
      ))}
    </div>
  );
}
