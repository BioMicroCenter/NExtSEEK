import json
import sys
import time
from pathlib import Path

from datetime import datetime
import streamlit as st

from chat_nextseek.config import ChatConfig
from chat_nextseek.orchestrator import run_query, run_query_plan

from chat_nextseek.tee import Tee

@st.cache_resource
def _get_config() -> ChatConfig:
    """Build and cache a single ChatConfig instance for the Streamlit process."""
    return ChatConfig()

config = _get_config()
MODEL_MODE = config.MODEL_MODE
LLM_MODEL = config.LLM_MODEL
NEXTSEEK_BASE_URL = config.NEXTSEEK_BASE_URL

def _streamlit_script_args(argv: list[str]) -> list[str]:
    """Return only app-level args, excluding Streamlit's own flags when present."""
    if "--" in argv:
        return argv[argv.index("--") + 1:]
    return argv[1:]


# Detect only app-level -p / --planner flags forwarded from cli.py.
_PLANNER_MODE = any(arg in ("-p", "--planner") for arg in _streamlit_script_args(sys.argv))


st.set_page_config(page_title="NExtSEEK Assistant", page_icon="🧬")
st.title("🧬 NExtSEEK Smart Query")
_mode_caption = f"Model mode: `{MODEL_MODE}`"
if _PLANNER_MODE:
    _mode_caption += "  |  **Planner pipeline** (`-p`)"
st.caption(_mode_caption)
CHAT_INPUT_KEY = "chat_input_text"
CHAT_PREFILL_KEY = "chat_input_prefill"

# ======================================================
# Session-scoped logging setup
# ======================================================

if "log_dir" not in st.session_state:
    ts = datetime.now().strftime("%y%m%d_%H%M%S")
    base_out = Path(config.OUTPUTS_DIR)
    base_out.mkdir(parents=True, exist_ok=True)
    log_dir = base_out / f"{ts}_{MODEL_MODE}"
    log_dir.mkdir(exist_ok=True)

    st.session_state["log_dir"] = str(log_dir)
    st.session_state["console_log_path"] = str(log_dir / "console.txt")
    st.session_state["chat_log_path"] = str(log_dir / "chat.txt")
    st.session_state["api_log_path"] = str(log_dir / "api_requests.json")
    st.session_state["prompts_log_path"] = str(log_dir / "prompts.json")

LOG_DIR = st.session_state["log_dir"]
CONSOLE_LOG = st.session_state["console_log_path"]
CHAT_LOG = st.session_state["chat_log_path"]
API_LOG = st.session_state["api_log_path"]
PROMPTS_LOG = st.session_state["prompts_log_path"]

if not st.session_state.get("console_tee_installed", False):
    sys.stdout = Tee(sys.stdout, CONSOLE_LOG)
    sys.stderr = Tee(sys.stderr, CONSOLE_LOG)
    st.session_state["console_tee_installed"] = True

if not st.session_state.get("config_snapshot_logged", False):
    try:
        config_snapshot = config.get_config_snapshot()
        print("[CONFIG] Snapshot:\n" + json.dumps(config_snapshot, indent=2))
    except Exception as e:
        print("[CONFIG] Failed to log config snapshot:", repr(e))
    st.session_state["config_snapshot_logged"] = True

# ======================================================
# Results history + API logging
# ======================================================

if "results_history" not in st.session_state:
    st.session_state["results_history"] = []

if "last_debug" not in st.session_state:
    st.session_state["last_debug"] = {}

if "query_events" not in st.session_state:
    st.session_state["query_events"] = []

# ---------- Session init ----------
if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []
    # Initialize chat log with a header
    try:
        with open(CHAT_LOG, "a", encoding="utf-8") as f:
            f.write(f"=== Chat session started at {datetime.now().isoformat()} ===\n")
    except Exception:
        pass

# ---------- Replay previous messages ----------
for m in st.session_state.chat_messages:
    with st.chat_message(m["role"]):
        # Let assistant messages render markdown+HTML if needed
        if m["role"] == "assistant":
            st.markdown(m["content"], unsafe_allow_html=True)
        else:
            st.markdown(m["content"])

# ---------- Apply any sidebar prefill request before rendering chat_input ----------
prefill_text = st.session_state.pop(CHAT_PREFILL_KEY, None)
if prefill_text is not None:
    st.session_state[CHAT_INPUT_KEY] = prefill_text

# ---------- Chat input ----------
user_text = st.chat_input("Ask me anything about NExtSEEK samples...", key=CHAT_INPUT_KEY)

if user_text:
    # Log user message to chat log
    try:
        with open(CHAT_LOG, "a", encoding="utf-8") as f:
            f.write(f"USER: {user_text}\n")
    except Exception:
        pass

    st.session_state.chat_messages.append({"role": "user", "content": user_text})
    with st.chat_message("user"):
        st.markdown(user_text)

    with st.chat_message("assistant"):
        _AGENT_STEP_LABELS = {
            "catalog":       "Shortlisting catalog",
            "entity":        "Extracting entities",
            "parser":        "Routing intent",
            "planner":       "Building execution plan",
            "context_engineer": "Bridging step context",
            "evaluator":     "Evaluating final answer",
            "api":           "Building API request",
            "reporter":      "Planning report",
            "report_writer": "Generating report",
            "memory":        "Retrieving session results",
            "system":        "Looking up system info",
            "graph":         "Generating graph query",
            "chatter":       "Summarizing results",
        }
        _SEARCH_STEP_LABELS = {
            "api":           "Querying NExtSEEK API",
            "neo4j":         "Querying graph database",
            "reporter_sql":  "Running SQL report",
        }

        with st.status("Working...", expanded=True) as _status:
            st.session_state["query_events"] = []

            def _event_label(kind: str, data: dict) -> str:
                if kind == "agent":
                    base = _AGENT_STEP_LABELS.get(data.get("agent", ""), data.get("agent", "agent"))
                else:
                    base = _SEARCH_STEP_LABELS.get(data.get("source", ""), data.get("source", "search"))
                step_id = data.get("step_id")
                if step_id is not None:
                    return f"{base} (step {step_id})"
                return base

            def _streamlit_send_event(event_type: str, data: dict) -> None:
                events = st.session_state.get("query_events", [])
                events.append({"event": event_type, "data": data})
                st.session_state["query_events"] = events
                if event_type == "agent_started":
                    _status.write(f"— {_event_label('agent', data)}...")
                elif event_type == "agent_complete":
                    complete_data = dict(data)
                    if isinstance(data.get("summary"), dict):
                        complete_data.update(data["summary"])
                    _status.write(f"— {_event_label('agent', complete_data)} complete")
                elif event_type == "search_started":
                    _status.write(f"— {_event_label('search', data)}...")
                elif event_type == "search_complete":
                    _status.write(f"— {_event_label('search', data)} complete")
                elif event_type == "query_complete":
                    _status.write("— Final response ready")
                elif event_type == "query_error":
                    agent = data.get("agent", "unknown")
                    _status.write(f"— Error in {agent}: {data.get('error', 'unknown error')}")

            _run_fn = run_query_plan if _PLANNER_MODE else run_query
            result = _run_fn(
                st.session_state,
                config,
                user_text,
                send_event=_streamlit_send_event,
            )
            reply = result["reply"]
            _status.update(label="Done", state="complete", expanded=False)

        st.markdown(reply, unsafe_allow_html=True)

    # Log assistant reply
    try:
        with open(CHAT_LOG, "a", encoding="utf-8") as f:
            f.write(f"ASSISTANT: {reply}\n")
    except Exception:
        pass

    st.session_state.chat_messages.append({"role": "assistant", "content": reply})

# ---------- File downloads (driven by last_files manifest) ----------

def _render_file_downloads(files: list[dict]) -> None:
    """Render a download button for each file in the manifest produced by run_query."""
    for f in files:
        path = f.get("path")
        if not path or not Path(path).exists():
            continue
        try:
            data = Path(path).read_bytes()
        except Exception:
            continue
        st.download_button(
            label=f["label"],
            data=data,
            file_name=f["filename"],
            mime=f.get("mime", "application/octet-stream"),
            key=f"dl_{f['key']}",
        )


# ---------- Result panels (display + downloads) ----------
history = st.session_state.get("results_history", [])
latest_bundle = history[-1] if history else None
last_files: list[dict] = st.session_state.get("last_files", [])

api_full = None
bundle_id = None

reporter_result = None
report_writer_output = None
if latest_bundle and latest_bundle.get("mode") == "reporter":
    reporter_result = latest_bundle.get("reporter_result")
    report_writer_output = latest_bundle.get("report_writer_output")
    bundle_id = latest_bundle.get("id")
elif latest_bundle and latest_bundle.get("mode") == "graph_query":
    bundle_id = latest_bundle.get("id")
    # graph results are rendered as markdown in chat; show cypher + meta in expander
else:
    if latest_bundle:
        api_full = latest_bundle.get("api_result_full")
        bundle_id = latest_bundle.get("id")
    else:
        last_debug = st.session_state.get("last_debug", {})
        api_full = (
            last_debug.get("api_result_full")
            or last_debug.get("api_result_norm")
            or last_debug.get("api_result_slim")
        )
        reporter_result = last_debug.get("reporter_result")
        report_writer_output = last_debug.get("report_writer_output")
        bundle_id = None

if reporter_result is not None:
    with st.expander("Reporter result", expanded=True):
        report_writer_output = report_writer_output or (reporter_result.get("report_writer_output") if isinstance(reporter_result, dict) else None)

        # Downloads from file manifest
        _render_file_downloads(last_files)

        if isinstance(reporter_result, dict) and reporter_result.get("rows_returned") is not None:
            st.write(f"Rows returned: {reporter_result.get('rows_returned')}")

            def _show_table(title, table_dict, sort_by_key=False):
                if not isinstance(table_dict, dict) or not table_dict:
                    st.write(f"{title}: n/a")
                    return
                items = list(table_dict.items())
                if sort_by_key:
                    items.sort(key=lambda kv: kv[0])  # chronological sort for date-like keys
                else:
                    items.sort(key=lambda kv: kv[1], reverse=True)
                rows = [{"key": k, "count": v} for k, v in items]
                st.write(title)
                st.table(rows)

            _show_table("SampleType Summary", reporter_result.get("sampletypes_table"))
            _show_table("Lab Summary", reporter_result.get("labs_table"))
            _show_table("Year Summary", reporter_result.get("years_table"))
            _show_table("Month Summary", reporter_result.get("months_table"), sort_by_key=True)
        else:
            # Narrative/report display without exposing raw metadata
            if report_writer_output:
                if isinstance(report_writer_output, dict) and not report_writer_output.get("report"):
                    st.write("Report JSON (per UID):")
                    st.json(report_writer_output)
                else:
                    narrative = report_writer_output.get("narrative")
                    if narrative:
                        st.write(narrative)
                    report_body = report_writer_output.get("report") or report_writer_output
                    st.write("Report JSON:")
                    st.json(report_body)
            else:
                st.write("Report payload:")
                st.json(reporter_result)

elif latest_bundle and latest_bundle.get("mode") == "graph_query":
    with st.expander("Graph Query Details", expanded=False):
        gp = latest_bundle.get("graph_plan") or {}
        gr = latest_bundle.get("graph_result") or {}
        if gp.get("cypher"):
            st.code(gp["cypher"], language="cypher")
        if gp.get("explanation"):
            st.caption(gp["explanation"])
        st.json({k: v for k, v in gr.items() if k != "data"})
        # Downloads from file manifest
        _render_file_downloads(last_files)

elif api_full is not None:
    with st.expander("Raw API result (JSON preview)", expanded=False):
        # Build a *trimmed* preview: only data.total + rows[idlink, json_metadata]
        data = api_full.get("data", {})
        rows = []

        if isinstance(data, dict):
            rows_source = data.get("rows") or data.get("rows_preview") or []
            if isinstance(rows_source, list):
                for r in rows_source[:25]:  # cap preview to first 25 rows
                    if not isinstance(r, dict):
                        continue
                    trimmed = {}
                    if "idlink" in r:
                        trimmed["idlink"] = r["idlink"]
                    if "json_metadata" in r:
                        trimmed["json_metadata"] = r["json_metadata"]
                    if trimmed:
                        rows.append(trimmed)

        preview_obj = {
            "total": data.get("total") if isinstance(data, dict) else None,
            "rows": rows,
        }

        # Pretty preview string
        if rows:
            try:
                preview_json_str = json.dumps(preview_obj, indent=2, ensure_ascii=False)
            except Exception:
                preview_json_str = str(preview_obj)
        else:
            # Fallback to full raw JSON when normalization/trim fails
            try:
                preview_json_str = json.dumps(api_full, indent=2, ensure_ascii=False)
            except Exception:
                preview_json_str = str(api_full)

        # ✅ IMPORTANT: force text_area to update when we have a new bundle_id
        prev_id = st.session_state.get("json_preview_last_bundle_id")
        if bundle_id is not None and prev_id != bundle_id:
            st.session_state["json_preview_last_bundle_id"] = bundle_id
            st.session_state["json_preview_textarea"] = preview_json_str

        # If no bundle_id (fallback path), still set if empty
        if bundle_id is None and "json_preview_textarea" not in st.session_state:
            st.session_state["json_preview_textarea"] = preview_json_str

        # Downloads from file manifest (above the preview box)
        _render_file_downloads(last_files)

        # Small, scrollable, pretty JSON box (trimmed preview)
        st.text_area(
            label="Raw JSON preview",
            value=st.session_state.get("json_preview_textarea", preview_json_str),
            height=240,
            key="json_preview_textarea",
            label_visibility="collapsed",
        )


# ---------- Sidebar debug ----------
with st.sidebar.expander("🔍 Debug (last turn)", expanded=False):
    st.json(st.session_state.get("last_debug", {}))

