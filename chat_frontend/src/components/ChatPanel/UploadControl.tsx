import { useRef, useState } from "react";
import { Paperclip, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import type { NextseekApiService } from "@/lib/services/chatApi";

const POLL_INTERVAL_MS = 2000;

interface UploadControlProps {
  apiService: NextseekApiService;
  disabled?: boolean;
  onUploadComplete?: () => void;
}

export function UploadControl({ apiService, disabled, onUploadComplete }: UploadControlProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    if (files.length > 0) {
      setSelectedFiles((prev) => [...prev, ...files]);
      setError(null);
    }
    e.target.value = "";
  };

  const removeFile = (index: number) => {
    setSelectedFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const handleUpload = async () => {
    if (selectedFiles.length === 0 || uploading) return;
    setUploading(true);
    setProgress(10);
    setError(null);
    try {
      const { job_id } = await apiService.uploadFiles(selectedFiles);
      setProgress(30);

      // eslint-disable-next-line no-constant-condition
      while (true) {
        const status = await apiService.pollUpload(job_id);
        if (status.state === "SUCCESS") {
          setProgress(100);
          setSelectedFiles([]);
          onUploadComplete?.();
          break;
        }
        if (status.state === "FAILURE") {
          throw new Error("Upload failed");
        }
        setProgress((p) => Math.min(p + 15, 90));
        await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      setTimeout(() => setProgress(0), 500);
    }
  };

  return (
    <div className="flex flex-col items-end gap-1" data-testid="upload-control">
      <input
        ref={inputRef}
        type="file"
        multiple
        className="hidden"
        onChange={handleFileChange}
        disabled={disabled || uploading}
      />
      <div className="flex items-center gap-1">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-8 w-8 p-0"
          aria-label="Attach files"
          disabled={disabled || uploading}
          onClick={() => inputRef.current?.click()}
        >
          <Paperclip className="h-4 w-4" />
        </Button>
        {selectedFiles.length > 0 && (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="h-7 text-xs"
            disabled={disabled || uploading}
            onClick={handleUpload}
          >
            Upload
          </Button>
        )}
      </div>
      {selectedFiles.length > 0 && (
        <ul className="max-w-[200px] space-y-0.5 text-right text-[10px] text-muted-foreground">
          {selectedFiles.map((f, i) => (
            <li key={`${f.name}-${i}`} className="flex items-center justify-end gap-1">
              <span className="truncate">{f.name}</span>
              {!uploading && (
                <button
                  type="button"
                  aria-label={`Remove ${f.name}`}
                  onClick={() => removeFile(i)}
                  className="shrink-0 hover:text-foreground"
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {uploading && progress > 0 && (
        <Progress value={progress} className="h-1 w-24" />
      )}
      {error && <p className="max-w-[200px] text-right text-[10px] text-destructive">{error}</p>}
    </div>
  );
}
