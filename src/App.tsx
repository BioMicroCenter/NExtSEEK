import { useCredentialCheck } from "./hooks";
import { AppLayout } from "./AppLayout";
import { Loader2 } from "lucide-react";

function App() {
  const { isReady, error } = useCredentialCheck();

  if (!isReady) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return <AppLayout credentialError={error} />;
}

export default App;
