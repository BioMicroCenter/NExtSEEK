// A stand-in for the Messages API for NessieAI/tests/cc/test_cc_resume_after_reset.py: it records every
// request body (one JSON line each) and answers "OK", streamed or not. No model is ever called.
const http = require("http");
const fs = require("fs");

const log = process.env.STUB_LOG || "/tmp/stub-requests.jsonl";
const port = Number(process.env.STUB_PORT || 8089);

http.createServer((req, res) => {
  const chunks = [];
  req.on("data", (chunk) => chunks.push(chunk));
  req.on("end", () => {
    const raw = Buffer.concat(chunks).toString("utf8");
    fs.appendFileSync(log, JSON.stringify({ method: req.method, url: req.url, body: raw }) + "\n");
    if (req.method !== "POST" || !req.url.startsWith("/v1/messages")) {
      res.writeHead(200, { "content-type": "application/json" });
      res.end("{}");
      return;
    }
    if (req.url.startsWith("/v1/messages/count_tokens")) {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ input_tokens: 1 }));
      return;
    }
    let body = {};
    try { body = JSON.parse(raw); } catch (e) { body = {}; }
    const message = {
      id: "msg_stub", type: "message", role: "assistant", model: body.model || "stub",
      content: [{ type: "text", text: "OK" }], stop_reason: "end_turn", stop_sequence: null,
      usage: { input_tokens: 1, output_tokens: 1 },
    };
    if (!body.stream) {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify(message));
      return;
    }
    res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
    const send = (event, data) => res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    send("message_start", { type: "message_start", message: { ...message, content: [], stop_reason: null } });
    send("content_block_start", { type: "content_block_start", index: 0, content_block: { type: "text", text: "" } });
    send("content_block_delta", { type: "content_block_delta", index: 0, delta: { type: "text_delta", text: "OK" } });
    send("content_block_stop", { type: "content_block_stop", index: 0 });
    send("message_delta", { type: "message_delta", delta: { stop_reason: "end_turn", stop_sequence: null }, usage: { output_tokens: 1 } });
    send("message_stop", { type: "message_stop" });
    res.end();
  });
}).listen(port, "127.0.0.1");
