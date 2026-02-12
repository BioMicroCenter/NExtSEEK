import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { TestCaseList } from "@/components/TestRunner/TestCaseList";
import type { TestCase } from "@/lib/types/api";

interface LeftSidebarProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  testCases: TestCase[];
  onRunTest: (testCase: TestCase) => void;
  onRunAllTests: () => void;
}

export function LeftSidebar({
  isOpen,
  onOpenChange,
  testCases,
  onRunTest,
  onRunAllTests,
}: LeftSidebarProps) {
  return (
    <Sheet open={isOpen} onOpenChange={onOpenChange}>
      <SheetContent side="left">
        <SheetHeader>
          <SheetTitle>Tests</SheetTitle>
          <SheetDescription>
            Run test queries against the NExtSEEK assistant
          </SheetDescription>
        </SheetHeader>

        <div className="space-y-4 px-4">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">Test Cases</p>
            <Button variant="outline" size="sm" onClick={onRunAllTests}>
              Run All
            </Button>
          </div>
        </div>

        <ScrollArea className="flex-1 px-2">
          <TestCaseList testCases={testCases} onRunTest={onRunTest} />
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}
