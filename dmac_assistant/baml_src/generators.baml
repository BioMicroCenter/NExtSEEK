// Top-level baml_src — dual codegen targets for bridge router and e2e judge.
//
// `baml-cli generate --from baml_src` writes:
//   - router client → src/dmac_assistant/router/baml_client/ (async default)
//   - e2e judge client → tools/e2e/baml_client/ (sync default; gitignored)
//
// Version aligned with pyproject.toml baml-py~=0.222.0. Distinct from
// vendor/chat_nextseek/.../baml_src/generators.baml (pins 0.221.0).

generator router_target {
    output_type "python/pydantic"
    output_dir "../src/dmac_assistant/router/"
    version "0.222.0"
    default_client_mode async
}

generator e2e_target {
    output_type "python/pydantic"
    output_dir "../tools/e2e/"
    version "0.222.0"
    default_client_mode sync
}
