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

