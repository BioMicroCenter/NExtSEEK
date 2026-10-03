# laya router contract (JevLevROUTING, fixed at stage 0)

Copied verbatim from PLAN.md section 0. Units read this; do not change it in a unit branch.

- `NessieAI/router/laya_calibration.json` (NEW, written by U3 in phase C; U1 reads it):
File formats (all fixed here):
- `NessieAI/router/laya_options.json` (NEW, written by U3's builder):
  `{"question_id":"route","prompt":"Which engine should answer the current message?","options":[{"key":"nextseek_query","text":"..."},{"key":"container_cc","text":"..."},{"key":"unrelated","text":"..."}],"source_hashes":{"<file>":"<sha256>"},"options_hash":"<sha256>"}`
- `NessieAI/router/laya_calibration.json` (NEW, written by U3 in phase C; U1 reads it):
  `{"revision":"<yyyymmdd>-<12 hex>","options_hash":"...","prompt_hash":"...","question_type":"choice","option_count":3,"temperature":<float>,"threshold":<float 0.5-0.99>,"fitted_on":{"n":<int>,"date":"<iso>"}}`
- Sidecar wire contract (U2 builds the wrapper, U1 builds the client; `laya-serve`'s own API is verified at build,
  SPEC s11, so the wrapper hides any difference): `POST http://laya-router:8080/route`, header
  `Authorization: Bearer <LAYA_API_KEY>`, JSON body `{"state":str,"question_id":"route","prompt":str,"options":{"<key>":"<text>",...}}`;
  reply `{"revision":str,"probabilities":{"<key>":float},"answer_confidence":float,"state_tokens":int,"truncated":bool}`
  (`probabilities` unrounded, as the app-side temperature needs them). `GET /health` returns `{"revision":str}`.
- `RouteDecision.laya` (dict or None, new optional field, added to `ROUTER_RECORD_FIELDS`): keys exactly as the SPEC
  s7 table: `mode, route, probabilities, answer_confidence, calibrated, calibrated_confidence, threshold, gate,
  latency_ms, revision, options_hash, prompt_hash, state_tokens, truncated, margin, error, baml_route`. `gate` is
  `"pass"` or one of: `timeout, http_error, bad_json, revision_mismatch, hash_mismatch, truncated, too_many_tokens,
  non_latin, followup_cc, below_threshold, unrelated, exception`.
- Held-out manifest row (U4 writes, U3 reads via `load_manifest`): `{"hash","split","family","entity","route","truth_kind"}`, no text.
- Training-view row (U3 `build_dataset.py` writes): `{"chat_id","query","history":[{"user_message","router_choice","status"}],"teacher_route","soft_teacher":{route:share}|null,"truth_route":str|null,"either":bool,"family","entity","slice":"train|calib|prompt_seen"}`.

