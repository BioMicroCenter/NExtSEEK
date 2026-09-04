"""Static file storage.

Why this file exists: the theme's CSS is referenced at a fixed URL
(`{% static 'css/nextseek.css' %}` in `themes/NextSeek/templates/base.html`) and
nginx serves `/static/` with `expires 30d` (`docker/nginx.conf:43`). With plain
`StaticFilesStorage` the URL never changes when the file does, so every deploy
that touches the theme leaves users on a stylesheet up to a month old while the
uncached HTML around it is new. The visible symptom is an unstyled page --
famously an enormous Nessie logo, because the rule constraining it is in a
stylesheet the browser is not going to re-fetch.

Content-hashed names fix that properly: the URL only exists for that exact
content, so `expires 30d` stops being a hazard and becomes correct.
"""

import sys

from django.contrib.staticfiles.storage import ManifestStaticFilesStorage


class ForgivingManifestStaticFilesStorage(ManifestStaticFilesStorage):
    """Hashed static filenames, without failing the build on an unmapped one.

    `manifest_strict = True` (Django's default) raises ValueError at REQUEST
    time for any `{% static %}` reference missing from the manifest. That is why
    ManifestStaticFilesStorage was reverted here on 2026-05-12: the vendored
    `jquery-easyui-1.5.2/` tree is not reachable by collectstatic in this
    layout, so its references are absent from the manifest and every page that
    names one 500s. The revert comment in dmac/settings.py records that a
    `manifest_strict = False` subclass is the intended fix; this is it.

    With it False, an unmapped reference falls back to the plain, unhashed URL.
    That is exactly today's behaviour for those files -- they stay cacheable for
    30 days at a fixed URL -- so nothing regresses, while every file
    collectstatic CAN see gains a content hash and updates the moment it
    changes. A missing reference costs that one file its cache-busting, never
    the page.
    """

    manifest_strict = False

    def stored_name(self, name):
        """The hashed name, or the plain one when it cannot be derived.

        `manifest_strict = False` alone is not enough. With no manifest entry it
        falls through to `hashed_name()`, which opens the file to hash it and
        raises ValueError when it is not on disk
        (django/contrib/staticfiles/storage.py:144). That turns a missing asset
        into a 500 on every page that references it -- which is strictly worse
        than the broken image it replaces.

        Falling back to the plain name keeps the page up and degrades exactly
        one asset. collectstatic still reports anything it could not hash, so
        this hides nothing at deploy time; it only stops a runtime page from
        dying over a decoration.
        """
        try:
            return super().stored_name(name)
        except ValueError:
            return self.clean_name(name)

    def post_process(self, paths, dry_run=False, **options):
        """Hash and rewrite what is decodable; pass the rest through untouched.

        Django rewrites the url() references inside every .css and .js it
        collects, which means decoding each one as UTF-8. Two vendored files in
        grappelli's bundled TinyMCE are not UTF-8 --
        `grappelli/tinymce/jscripts/tiny_mce/plugins/spellchecker/editor_plugin.js`
        and its `_src` twin, measured 2026-09-04, the only two in the whole
        collected tree -- and one UnicodeDecodeError there aborts collectstatic
        for everything. Since the entrypoint treats a failed collectstatic as
        fatal (docker/scripts/entrypoint.sh:13), that is a container that will
        not boot.

        A file nobody can decode is one whose references cannot be rewritten
        anyway, so it is dropped from the batch and reported as skipped. It
        keeps its plain name, which is what `manifest_strict = False` above
        already makes safe to reference.

        Deliberately a filter rather than a try/except around the whole batch:
        the aim is to lose the two files that cannot work, not to lose hashing
        for every file after the first bad one.
        """
        undecodable = {}
        for name in list(paths):
            if not name.lower().endswith((".css", ".js")):
                continue
            storage, path = paths[name]
            try:
                with storage.open(path) as handle:
                    handle.read().decode("utf-8")
            except UnicodeDecodeError:
                undecodable[name] = paths.pop(name)
            except Exception:
                # Unreadable for some other reason is not this method's problem;
                # leave it in the batch and let the normal path report it.
                continue

        # post_process YIELDS its errors as the third element rather than
        # raising them; collectstatic then re-raises the first one it sees
        # (django/contrib/staticfiles/management/commands/collectstatic.py:149-153).
        # Intercepting here is therefore the only place a broken reference can be
        # downgraded without also swallowing a real failure.
        unresolved = []
        for name, hashed_name, processed in super().post_process(paths, dry_run,
                                                                 **options):
            if isinstance(processed, ValueError) and "could not be found" in str(processed):
                # A url() inside a vendored stylesheet naming a file that was
                # never shipped -- e.g. a webfont this project does not carry.
                # It is broken with or without hashing, and it must not be the
                # reason a container refuses to boot. That one file keeps its
                # plain name; everything else is still hashed.
                unresolved.append((name, str(processed)))
                yield name, name, False
                continue
            yield name, hashed_name, processed

        for name in undecodable:
            # (original, processed, processed?) -- a falsy third element makes
            # collectstatic log it as skipped rather than post-processed.
            yield name, name, False

        # Loud on purpose. These are silent cache-busting holes, and the day one
        # of them is OUR stylesheet rather than a vendored one, this line is what
        # says so.
        if unresolved:
            sys.stderr.write(
                f"[static] {len(unresolved)} file(s) kept an unhashed name "
                f"because they reference a file that is not collected:\n"
            )
            for name, detail in unresolved:
                sys.stderr.write(f"[static]   {name}: {detail}\n")
