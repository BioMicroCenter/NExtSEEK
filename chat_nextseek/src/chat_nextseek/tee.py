class Tee:
    """
    Tee stdout/stderr to both the original stream and a log file.
    Uses a persistent file handle for efficiency.
    """

    def __init__(self, stream, path: str):
        """Open the destination log file and remember the original output stream."""
        self.stream = stream
        self.path = path
        self._file = None
        try:
            self._file = open(path, "a", encoding="utf-8")
        except Exception:
            pass

    def write(self, data):
        """Write output to both the wrapped stream and the log file when available."""
        self.stream.write(data)
        if data and self._file:
            try:
                self._file.write(data)
                self._file.flush()
            except Exception:
                pass

    def flush(self):
        """Flush both the wrapped stream and the log file, ignoring secondary failures."""
        try:
            self.stream.flush()
        except Exception:
            pass
        if self._file:
            try:
                self._file.flush()
            except Exception:
                pass

    def close(self):
        """Close the tee log file without closing the wrapped stream."""
        if self._file:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None
