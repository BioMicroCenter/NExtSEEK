import { createRoot } from "react-dom/client";
import "./index.embedded.css";
import { EmbeddedApp } from "./EmbeddedApp";

const container = document.getElementById("chat-assistant-root");
if (container) {
  createRoot(container).render(<EmbeddedApp />);
}
