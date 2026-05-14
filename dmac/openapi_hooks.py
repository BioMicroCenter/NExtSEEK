from rest_framework.versioning import AcceptHeaderVersioning


def exclude_seek_paths(endpoints):
    """Filter out endpoints from OpenAPI schema.

    Currently excludes:
    - legacy /seek/ endpoints
    - Assistant REST endpoints (registered under /assistant/ and mounted under /nextseek_api/)
    """
    return [
        (path, path_regex, method, callback)
        for path, path_regex, method, callback in endpoints
        if not (
            path.startswith('/seek/')
            # Depending on how URL patterns are resolved, drf-spectacular may
            # see assistant paths with or without the /nextseek_api mount prefix.
            or path.startswith('/assistant/')
            or path.startswith('/nextseek_api/assistant/')
        )
    ]


# Module-level tracker for the PREPROCESSING/POSTPROCESSING swap pair.
# Populated by swap_versioning_for_schema_gen, drained by restore_versioning_post_schema_gen.
# Both hooks run sequentially in the same `spectacular` management-command process — no
# thread-local needed.
_swapped_views = []


def swap_versioning_for_schema_gen(endpoints):
    """PREPROCESSING_HOOK — replace VendorMediaTypeVersioning with stock AcceptHeaderVersioning
    on each view, for the duration of schema generation only.

    Why: drf-spectacular 0.29.0's `is_versioning_supported()` only recognizes a hardcoded
    tuple of versioning classes (URLPathVersioning, NamespaceVersioning, AcceptHeaderVersioning).
    Our `VendorMediaTypeVersioning(BaseVersioning)` is rejected with a "unsupported versioning
    class" warning, and spectacular processes every view as unversioned.

    Additionally, even subclassing `AcceptHeaderVersioning` directly would fail because
    `operation_matches_version()` (plumbing.py:1052-1058) does `version, _ = view.determine_version(...)`
    expecting a 2-tuple, but DRF versioning classes return a single string.

    The swap pattern avoids both bugs: spectacular sees stock AcceptHeaderVersioning;
    runtime sees our custom class. The two contexts never overlap.
    """
    from nextseek_api.versioning import VendorMediaTypeVersioning

    _swapped_views.clear()

    for path, path_regex, method, callback in endpoints:
        view_cls = getattr(callback, 'cls', None)
        if view_cls is not None and getattr(view_cls, 'versioning_class', None) is VendorMediaTypeVersioning:
            _swapped_views.append(view_cls)
            view_cls.versioning_class = AcceptHeaderVersioning

    return endpoints


def restore_versioning_post_schema_gen(result, generator, request, public):
    """POSTPROCESSING_HOOK — restore the original VendorMediaTypeVersioning on each view
    that swap_versioning_for_schema_gen patched, then strip the `; version=X` parameter
    that AcceptHeaderVersioning appends to every media type in the generated schema.

    Why strip: AcceptHeaderVersioning encodes the version into the Accept header parameter
    (`Accept: application/foo; version=v2`), and drf-spectacular reflects that into every
    `content` key as `application/foo; version=v2`. But production runtime uses
    VendorMediaTypeVersioning, which responds with bare `application/foo` (no parameter).
    The schema must document what production actually serves, not the AcceptHeader form.
    Side effect: all `OpenApiExample(media_type="application/vnd.nextseek.v2+json", ...)`
    entries can now attach (their bare media_type strings match the bare schema keys).
    """
    from nextseek_api.versioning import VendorMediaTypeVersioning

    for view_cls in _swapped_views:
        view_cls.versioning_class = VendorMediaTypeVersioning
    _swapped_views.clear()

    def _strip(media_type: str) -> str:
        return media_type.split(";", 1)[0].strip()

    def _rekey_content(content: dict) -> dict:
        if not isinstance(content, dict):
            return content
        out: dict = {}
        for key, value in content.items():
            new_key = _strip(key)
            if new_key in out:
                existing = out[new_key]
                if isinstance(existing, dict) and isinstance(value, dict):
                    merged = dict(existing)
                    for k, v in value.items():
                        if k == "examples" and isinstance(v, dict):
                            merged_examples = dict(existing.get("examples", {}))
                            merged_examples.update(v)
                            merged["examples"] = merged_examples
                        else:
                            merged.setdefault(k, v)
                    out[new_key] = merged
                # else: keep first occurrence
            else:
                out[new_key] = value
        return out

    for path_item in (result.get("paths") or {}).values():
        if not isinstance(path_item, dict):
            continue
        for method, op in path_item.items():
            if method not in ("get", "post", "put", "patch", "delete", "head", "options"):
                continue
            if not isinstance(op, dict):
                continue
            req_body = op.get("requestBody")
            if isinstance(req_body, dict) and isinstance(req_body.get("content"), dict):
                req_body["content"] = _rekey_content(req_body["content"])
            for resp in (op.get("responses") or {}).values():
                if isinstance(resp, dict) and isinstance(resp.get("content"), dict):
                    resp["content"] = _rekey_content(resp["content"])

    return result
