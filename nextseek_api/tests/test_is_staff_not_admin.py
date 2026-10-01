"""is_staff must not confer admin privilege (#74, #75).

`dmac.views.userSynchronization` sets ``is_staff = 1`` on every SEEK user at
login (dmac/views.py:80 and :97), and no live application path ever assigns
``is_superuser``. Any authorization predicate that accepts ``is_staff`` is
therefore equivalent to ``IsAuthenticated``.

Two distinct shapes are guarded here, and they fail in opposite directions:

* **#74 — a query-branch selector.** The sample download API
  (``samples/retrieve/`` and its ``admin/samples/retrieve/`` alias) is
  ``[IsAuthenticated]`` by design (``IsAdminUser`` was removed deliberately in
  2690598). Its admin flag (``resolve_scope``) picks between unscoped rows and
  the caller's projects. Accepting ``is_staff`` there made project scope
  a no-op for every account. Nobody is denied by the fix — they are scoped.
* **#75 — a gate.** ``EvaluatorViewSet``'s read endpoints return other users'
  assistant prompts and result bundles. Accepting ``is_staff`` there exposed
  them to every authenticated account. The fix denies.
"""

import pathlib

from django.test import SimpleTestCase

REPO = pathlib.Path(__file__).resolve().parents[2]


class StaffIsNotAdminForSampleScope(SimpleTestCase):
    """#74 — the sample download API's admin flag must read is_superuser alone."""

    def _scope(self, **flags):
        from types import SimpleNamespace
        from unittest.mock import patch

        from nextseek_api.services import sample_retrieve

        user = SimpleNamespace(username="someone", **flags)
        # A non-superuser's membership read is stubbed: the question here is only whether they are unscoped.
        with patch("nextseek_api.graph_search.scope.connections") as conns:
            cursor = conns.__getitem__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = (100,)
            cursor.fetchall.return_value = [(2,)]
            return sample_retrieve._caller_scope(SimpleNamespace(user=user))

    def test_admin_flag_does_not_accept_is_staff(self):
        scope = self._scope(is_staff=True, is_superuser=False)
        self.assertFalse(scope.is_admin, "is_staff made the download API unscoped: every SEEK user has it")
        self.assertEqual(scope.project_ids, (2,))

    def test_admin_flag_reads_is_superuser(self):
        self.assertTrue(self._scope(is_staff=False, is_superuser=True).is_admin)

    def test_retrieve_endpoint_is_not_gated_shut(self):
        """The #74 fix must SCOPE, never DENY.

        Swapping the permission class to a superuser gate would 403 every
        non-superuser and break the assistant, which calls this endpoint as the
        requesting user. Keep it IsAuthenticated.
        """
        from rest_framework.permissions import IsAuthenticated

        from nextseek_api.views import AdminSampleViewSet, SampleRetrieveViewSet

        for viewset in (SampleRetrieveViewSet, AdminSampleViewSet):
            names = {c.__name__ for c in viewset.permission_classes}
            self.assertIn(IsAuthenticated.__name__, names)
            self.assertNotIn("IsAdminUser", names)
            self.assertNotIn("IsSuperUser", names)

    def test_project_scoped_branch_still_exists(self):
        """The `else` branch the fix switches traffic onto must remain intact."""
        src = (REPO / "seek" / "dbtable_sample.py").read_text()
        start = src.index("def getChildrenUIDs")
        body = src[start : start + 2500]
        self.assertIn("projects_samples", body)
        self.assertIn("project_id IN", body)


class StaffIsNotAdminForEvaluatorReads(SimpleTestCase):
    """#75 — evaluator reads expose other users' history and must be superuser-only."""

    def test_evaluator_does_not_use_is_admin_user(self):
        from nextseek_api.services.evaluator import EvaluatorViewSet

        names = {c.__name__ for c in EvaluatorViewSet.permission_classes}
        self.assertNotIn(
            "IsAdminUser",
            names,
            "IsAdminUser checks is_staff, which every SEEK user has — these reads "
            "return other users' assistant prompts and result bundles.",
        )

    def test_evaluator_requires_superuser(self):
        from nextseek_api.permissions import IsSuperUser
        from nextseek_api.services.evaluator import EvaluatorViewSet

        self.assertIn(IsSuperUser, EvaluatorViewSet.permission_classes)

    def test_superuser_predicate_rejects_staff_only_user(self):
        """Directly exercise the predicate the gate now relies on."""
        from django.contrib.auth.models import AnonymousUser

        from nextseek_api.permissions import IsSuperUser

        class _User:
            is_authenticated = True

            def __init__(self, staff, superuser):
                self.is_staff = staff
                self.is_superuser = superuser

        class _Req:
            def __init__(self, user):
                self.user = user

        perm = IsSuperUser()
        self.assertFalse(perm.has_permission(_Req(_User(True, False)), None))
        self.assertTrue(perm.has_permission(_Req(_User(True, True)), None))
        self.assertTrue(perm.has_permission(_Req(_User(False, True)), None))
        self.assertFalse(perm.has_permission(_Req(AnonymousUser()), None))


class StaffAdminWideningIsConfinedToCapabilities(SimpleTestCase):
    """Document where `is_staff or is_superuser` deliberately survives.

    These are capability toggles and a static catalog, not data scope, so #74/#75
    do not change them. This test pins the inventory: if a new data-scope site
    starts accepting is_staff, the count moves and someone has to look.
    """

    ALLOWED = {
        # The batch-upload lababbv and person_id overrides read is_superuser alone
        # since 2026-09-15 (IsSuperUser), so they are no longer in this inventory.
        # "is_admin reported to the UI" was removed 2026-08-20: that flag drives the
        # Admin badge AND the Debug panel (PROD toggle, force_route, max-turn-length),
        # and the PROD toggle selects a ChatConfig that authenticates as a superuser
        # service account -- so it was a privilege escalation, not a capability toggle.
        ("nextseek_api/services/assistant.py", "static test-case catalog"),
    }

    def test_known_capability_sites_only(self):
        import re

        pattern = re.compile(r"is_staff\s+or\s+.*is_superuser|is_superuser\s+or\s+.*is_staff")
        found = []
        # NessieAI/router holds the force_route admin gate (_decide_route in
        # policy.py), moved out of nextseek_api/services/cc_assistant.py in
        # Phase B; scanning it keeps that gate inside this inventory. The
        # use_prod gate (_select_chat_config) moved to NessieAI/ns/turn.py the
        # same way, out of nextseek_api/services/assistant.py, and the
        # max-turn-length gate to NessieAI/cc/turn.py.
        for rel in (
            "nextseek_api", "seek", "dmac",
            "NessieAI/router", "NessieAI/ns", "NessieAI/cc",
        ):
            for path in (REPO / rel).rglob("*.py"):
                if "test" in path.parts or path.name.startswith("test_"):
                    continue
                for i, line in enumerate(path.read_text().splitlines(), 1):
                    if line.lstrip().startswith("#"):
                        continue
                    if pattern.search(line):
                        found.append(f"{path.relative_to(REPO)}:{i}")

        self.assertEqual(
            len(found),
            len(self.ALLOWED),
            "The set of is_staff-as-admin sites changed. Every one of these must be a "
            "capability toggle, never a data-scope decision — a data-scope site here is "
            f"a #74 regression. Found: {found}",
        )
