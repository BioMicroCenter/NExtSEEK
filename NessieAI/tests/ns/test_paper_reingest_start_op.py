"""The paper-reingest-start endpoint: 200 with the op's result, 422 on a refusal, 422 on an unknown key.

Reached the way test_granular_endpoints.py reaches every granular op (its client, credentials and
allowlist patch), as a superuser. The op body (NessieAI/ns/paper/start.py) is patched; its own tests
are NessieAI/tests/ns/paper/test_start.py.
"""
import os
from unittest.mock import patch

from NessieAI.ns.paper.start import StartRefused
from NessieAI.tests.ns.test_granular_endpoints import GranularEndpointBase


class PaperReingestStartEndpointTests(GranularEndpointBase):
    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.client.force_authenticate(user=self.user)

    def test_superuser_starts_a_run(self):
        with patch("NessieAI.ns.paper.start.start",
                   return_value={"run_id": "r1", "status": "ok", "reply": "Started."}) as start:
            resp = self.client.post(f"{self.BASE}/paper-reingest-start/", {"paper": "38000001"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["op"], "paper-reingest-start")
        self.assertEqual(body["result"]["run_id"], "r1")
        paper, project, session = start.call_args.args
        self.assertEqual((paper, project), ("38000001", None))
        self.assertEqual(session.user.pk, self.user.pk)

    def test_refusal_is_a_validation_error(self):
        with patch("NessieAI.ns.paper.start.start", side_effect=StartRefused("off", "feature_off")):
            resp = self.client.post(f"{self.BASE}/paper-reingest-start/", {"paper": "38000001"}, format="json")
        self.assertEqual(resp.status_code, 422, resp.content)
        self.assertEqual(resp.json()["code"], "VALIDATION")

    def test_extra_keys_rejected(self):
        with patch("NessieAI.ns.paper.start.start") as start:
            resp = self.client.post(f"{self.BASE}/paper-reingest-start/",
                                    {"paper": "1", "confirmed_write": True}, format="json")
        self.assertEqual(resp.status_code, 422, resp.content)
        start.assert_not_called()


class PaperReingestStartRealGateTests(GranularEndpointBase):
    """The real start body, unpatched: its two gates answer before any lookup."""

    def _refusal_type(self, resp):
        self.assertEqual(resp.status_code, 422, resp.content)
        self.assertEqual(resp.json()["code"], "VALIDATION")
        return resp.json()["errors"][0]

    def test_staff_who_is_not_a_superuser_is_refused(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_authenticate(user=self.user)
        with patch.dict(os.environ, {"NEXTSEEK_PAPER_REINGEST": "1"}):
            resp = self.client.post(f"{self.BASE}/paper-reingest-start/", {"paper": "38000001"}, format="json")
        self.assertEqual(self._refusal_type(resp), {"field": "paper", "type": "superusers_only"})

    def test_superuser_without_the_feature_flag_is_refused(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.client.force_authenticate(user=self.user)
        env = {k: v for k, v in os.environ.items() if k != "NEXTSEEK_PAPER_REINGEST"}
        with patch.dict(os.environ, env, clear=True):
            resp = self.client.post(f"{self.BASE}/paper-reingest-start/", {"paper": "38000001"}, format="json")
        self.assertEqual(self._refusal_type(resp), {"field": "paper", "type": "feature_off"})
