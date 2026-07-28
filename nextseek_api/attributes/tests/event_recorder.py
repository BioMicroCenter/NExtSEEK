from __future__ import annotations
import argparse
import json
import os
import signal
from pathlib import Path
from kombu import Connection
from nextseek_api.batch_upload.celery_app import app

running = True


def stop(*_args):
    global running
    running = False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--broker", required=True)
    parser.add_argument("--queue", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream, Connection(args.broker) as connection:
        def record(event):
            routing = event.get("queue") or event.get("routing_key") or event.get("exchange")
            if routing != args.queue and event.get("type") != "attribute-fault":
                return
            row = {"type": event.get("type"), "uuid": event.get("uuid") or event.get("fault_point"),
                   "queue": routing or args.queue, "worker_pid": event.get("pid")}
            stream.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        receiver = app.events.Receiver(connection, handlers={"*": record}, app=app)
        while running:
            try:
                receiver.capture(limit=None, timeout=1, wakeup=True)
            except TimeoutError:
                pass


if __name__ == "__main__":
    main()
