"""Operator CLI for paper reingest runs: start one, read its state, export an artifact.

`start --inline` runs stage 1 in this process instead of on the worker, which is how a
golden paper's evidence.json is captured for the piece-2 tests.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from NessieAI.ns.paper.fetch import Fetcher
from NessieAI.ns.paper.launch import LaunchSeams, launch_check
from NessieAI.ns.paper.models import EvidenceArtifact, PaperArtifact
from NessieAI.ns.paper.store import load_artifact
from nextseek_api.paper_reingest import service, tasks
from nextseek_api.paper_reingest.lookups import doi_project_ids_strict
from nextseek_api.paper_reingest.models_db import PaperReingestRun

_MODELS = {"paper": PaperArtifact, "evidence": EvidenceArtifact}


class Command(BaseCommand):
    help = "Start, inspect and export paper reingest runs."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="verb", required=True)
        start = sub.add_parser("start")
        start.add_argument("--user", required=True)
        start.add_argument("--paper", required=True)
        start.add_argument("--project", type=int)
        start.add_argument("--inline", action="store_true")
        status = sub.add_parser("status")
        status.add_argument("run_id")
        export = sub.add_parser("export")
        export.add_argument("run_id")
        export.add_argument("artifact", choices=sorted(_MODELS))
        export.add_argument("--out", required=True)

    def handle(self, *args, verb, **opts):
        getattr(self, f"_{verb}")(**opts)

    def _start(self, *, user, paper, project, inline, **_):
        owner = get_user_model().objects.filter(username=user).first()
        if owner is None or not owner.is_superuser:
            raise CommandError("--user must name an existing superuser")
        seams = LaunchSeams(fetcher=Fetcher(), input_files=set(),
                            read_input_file=lambda name: open(name, "rb").read(),
                            doi_project_ids=doi_project_ids_strict)
        outcome = launch_check(paper, seams)
        if outcome.status != "ok":
            raise CommandError(f"{outcome.status}: {outcome.reply}")
        run = service.create_run(user=owner, paper_input=paper, paper=outcome.paper, project_hint=project)
        if inline:
            result = tasks.run_acquire(run.run_id, retries_done=0, max_retries=0)
            self.stdout.write(f"{run.run_id}  {result}")
        else:
            service.enqueue_acquire(run)
            self.stdout.write(f"{run.run_id}  queued")

    def _status(self, *, run_id, **_):
        run = PaperReingestRun.objects.filter(run_id=run_id).first()
        if run is None:
            raise CommandError(f"no run {run_id}")
        line = f"{run.run_id}  {run.state}  {run.mode}  attempts={run.attempts}  {run.title[:80]}"
        if run.reason:
            line += f"\n  {run.failed_stage or run.state}: {run.reason}"
        if "evidence" in run.artifacts:
            ev = load_artifact(run_id, run.artifacts["evidence"], EvidenceArtifact)
            line += (f"\n  items={len(ev.items)} deposits={len(ev.deposits)} skeleton={len(ev.skeleton)} "
                     f"gaps={len(ev.gaps)}")
        self.stdout.write(line)

    def _export(self, *, run_id, artifact, out, **_):
        run = PaperReingestRun.objects.filter(run_id=run_id).first()
        if run is None or artifact not in run.artifacts:
            raise CommandError(f"run {run_id} has no {artifact} artifact")
        model = load_artifact(run_id, run.artifacts[artifact], _MODELS[artifact])
        with open(out, "w", encoding="utf-8") as handle:
            handle.write(model.model_dump_json(indent=2))
        self.stdout.write(f"wrote {out}")
