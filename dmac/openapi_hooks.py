def exclude_seek_paths(endpoints):
    """Filter out legacy /seek/ endpoints from OpenAPI schema."""
    return [
        (path, path_regex, method, callback)
        for path, path_regex, method, callback in endpoints
        if not path.startswith('/seek/')
    ]

