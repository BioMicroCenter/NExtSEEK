from chat_nextseek.helpers.tools.nextseek_api import build_recent_results_summary


def test_summary_includes_bundle_mode():
    session = {
        "results_history": [
            {"id": 1, "user_query": "mice in GBM study", "endpoint": "neo4j",
             "mode": "graph_query", "api_result_slim": {}},
        ]
    }
    out = build_recent_results_summary(session)
    assert "mode=graph_query" in out
    assert "id=1" in out
