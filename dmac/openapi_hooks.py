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
    that swap_versioning_for_schema_gen patched. Runs after spectacular generates the schema.
    """
    from nextseek_api.versioning import VendorMediaTypeVersioning

    for view_cls in _swapped_views:
        view_cls.versioning_class = VendorMediaTypeVersioning
    _swapped_views.clear()

    return result
