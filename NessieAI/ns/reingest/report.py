"""Render the QA outcome as something a scientist can act on.

Three rules, each learned from the version that leaked developer output into a
chat window:

* Count, do not enumerate. 24 rows sharing a finding is one sentence; a list
  only when the items genuinely differ.
* Name the measurement, not the key. The raw MultiQC column belongs in the
  Provenance sheet and the approval queue, not here.
* Every flag ends in a choice the reader can make.

The server renders this and the agent relays it VERBATIM. Letting the model
re-summarise makes the wording different every run and loses the calibration.
"""
from __future__ import annotations

from NessieAI.ns import reingest_qa as qa

# Human names for the attributes a reader will actually see flagged, cased
# exactly as they should appear at the start of a sentence. Curated here
# rather than derived with `_cap`/str.capitalize(), both of which mangle a
# name like "rRNA content" -- capitalize() lowercases the tail into "Rrna
# content", and even the first-character-only `_cap` turns it into "RRNA
# content". These values are used verbatim, never passed through either.
_FRIENDLY = {
    "ContamPercent": "Contamination percentage",
    "MappedPercent": "Mapping rate",
    "rRNAPercent": "rRNA content",
    "GenesDetected": "Genes detected",
    "Strandedness": "Strandedness",
    "DuplicationPercent": "Duplication rate",
    "File_PrimaryData": "the file path",
    "Link_PrimaryData": "the download link",
}

# UNRESOLVED_UID lists affected samples by name; cap it so a genuinely large
# batch doesn't turn into the exact log-in-a-chat-window leak rule 1 forbids.
_MAX_SAMPLES_LISTED = 10


def _cap(s: str) -> str:
    """Capitalize only the first character. str.capitalize() also lowercases
    the tail, which would mangle a bare attribute like "MedianCV" that is
    already correctly cased. Used only as `_name`'s fallback for an attribute
    with no curated `_FRIENDLY` entry -- a curated name is already cased
    correctly and must never be run through this."""
    return s[:1].upper() + s[1:] if s else s


def _name(attribute: str) -> str:
    """Sentence-ready display name for `attribute`: the curated friendly name
    verbatim, or `_cap` applied to the raw attribute when there is none."""
    friendly = _FRIENDLY.get(attribute)
    return friendly if friendly is not None else _cap(attribute)


def _plural(count: int, word: str = "row") -> str:
    return word if count == 1 else f"{word}s"


def _verb(count: int, plural_form: str, singular_form: str | None = None) -> str:
    """Subject-verb agreement for "{count} row(s) <verb> ...": a plural count
    takes `plural_form` ("name", "claim", "are"), a count of exactly one
    takes `singular_form` (defaults to `plural_form` + "s")."""
    if count == 1:
        return singular_form if singular_form is not None else f"{plural_form}s"
    return plural_form


def _workbook_ref(sample_type: str) -> str:
    return f"the {sample_type} workbook" if sample_type else "this workbook"


def _is_backfill(key: str) -> bool:
    """A backfill/update workbook, by the one naming convention the caller
    guarantees: its artifact key ends in "_update". Used both to sort TO
    UPLOAD (children before the backfill) and to choose its instruction --
    from one place, so the two can never disagree."""
    return key.endswith("_update")


def _normalize_artifact_key(name: str) -> str:
    """Duplicate of `NessieAI/ns/granular.py`'s `safe_key` normalisation
    (dot, hyphen, slash and space -> underscore), kept in sync by hand
    rather than imported: report.py may import only `NessieAI.ns.reingest_qa`
    and the stdlib, and importing granular.py would be a new coupling.

    If you change this, change granular.py's `safe_key` line too -- a
    mismatch means a sample type with a hyphen/space/slash (e.g.
    "A.MADE-UP-TYPE") normalises to a different key here than the one
    granular.py actually saved the workbook under, so `_resolve_artifact`
    matches nothing and the workbook silently drops out of both the status
    list and TO UPLOAD."""
    return name.replace(".", "_").replace("-", "_").replace("/", "_").replace(" ", "_")


def _resolve_artifact(artifacts: dict, sample_type: str):
    """Match a sample type to its workbook, anchored on the exact key first
    and the "_update" backfill variant second -- never a bare substring test,
    which would let sample type "A.GEX" match artifact key
    "reingest_A.GEXPLUS" before "reingest_A.GEX" depending on dict order."""
    norm = _normalize_artifact_key(sample_type)
    exact = f"reingest_{norm}"
    update = f"{exact}_update"
    for key, path in artifacts.items():
        if _normalize_artifact_key(key) == exact:
            return key, path
    for key, path in artifacts.items():
        if _normalize_artifact_key(key) == update:
            return key, path
    return None, None


def _artifact_name(artifacts: dict, sample_type: str) -> str:
    _, path = _resolve_artifact(artifacts, sample_type)
    return path.rsplit("/", 1)[-1] if path else sample_type


def _children_first(item):
    return (0 if item[0].startswith("A.") else 1, item[0])


def render_qa_for_user(reports: dict[str, qa.QaReport], artifacts: dict[str, str],
                        run_name: str) -> str:
    """Render the whole run's QA outcome as plain-language text.

    ``reports`` -- ``{sample_type: QaReport}``, one entry per sample type QA'd
    this run (see ``NessieAI/ns/reingest_qa.qa_rows``).

    ``artifacts`` -- ``{artifact_key: path}``, the rendered workbooks for this
    run, keyed exactly as ``NessieAI/ns/granular.py``'s ``_build_upload_xlsx``
    builds ``saved_files`` (``reingest_<sample_type with "." "-" "/" " " ->
    "_">``, optionally suffixed ``_update`` for a backfill workbook). Looked up
    per sample type via ``_resolve_artifact``/``_normalize_artifact_key``,
    which duplicate that same normalisation by hand.

    Returns the full report as one string. This is the module's only public
    function, and its output is relayed to the user VERBATIM by the calling
    agent -- see the module docstring's three rules -- so nothing here should
    be re-summarised or reworded downstream.
    """
    blocked = any(built.disposition == qa.HARD_REJECT for built in reports.values())
    lines: list[str] = []

    if blocked:
        lines.append(f"Reingest blocked — run {run_name}")
    else:
        lines.append(f"Reingest ready — run {run_name}")
    lines.append("")

    # One ordering for both the status list and the findings below it, so a
    # sample type can't appear in a different position in each.
    ordered = sorted(reports.items(), key=_children_first)

    for sample_type, built in ordered:
        mark = {"CLEAN": "OK", "SOFT_FLAG": "!", "HARD_REJECT": "X"}[built.disposition]
        name = _artifact_name(artifacts, sample_type)
        lines.append(f"  {mark}  {name}")
    lines.append("")

    # Sample types where a missing Parent is itself hard-blocking this run
    # (qa.MISSING_REQUIRED on "Parent" -- see qa.reingest_qa's
    # _ALWAYS_HARD_REQUIRED). A row with no parent-ish key at all trips BOTH
    # this and the three-state LINEAGE_UNRESOLVED SOFT finding below, and
    # LINEAGE_UNRESOLVED's own wording ("upload as-is, attach the parent
    # later") is only true when nothing else blocks the workbook -- so its
    # renderer needs to know, per sample type, whether that is still the
    # case. See _render_one's qa.LINEAGE_UNRESOLVED branch.
    parent_blocked_types = {
        sample_type for sample_type, built in ordered
        if any(f.code == qa.MISSING_REQUIRED and f.attribute == "Parent"
               for f in built.findings)
    }

    hard_checks: list[tuple] = []
    soft_checks: list[tuple] = []
    for sample_type, built in ordered:
        for (code, attribute), bucket in qa.group(built.findings).items():
            # The Finding itself often leaves sample_type blank for row-level
            # checks (a blank Parent, a duplicate name, ...); the QaReport
            # we're iterating is already scoped to one sample type, which is
            # the reliable source, so it wins over whatever group() carried.
            bucket["sample_type"] = sample_type
            # Route explicitly on the two known severities and fail loudly on
            # anything else, matching QaReport.add()'s own philosophy: a gate
            # whose job is blocking bad uploads must not quietly file an
            # unknown severity under "soft" (or "hard") and render it as
            # something milder (or harsher) than it is.
            if bucket["severity"] == qa.HARD:
                target = hard_checks
            elif bucket["severity"] == qa.SOFT:
                target = soft_checks
            else:
                raise ValueError(
                    f"unknown finding severity: {bucket['severity']!r}")
            target.append((code, attribute, bucket))

    if hard_checks:
        lines.append("WHAT IS BLOCKING")
        lines.append("")
        for index, (code, attribute, bucket) in enumerate(hard_checks, start=1):
            lines.extend(_render_one(index, code, attribute, bucket, parent_blocked_types))
            lines.append("")

    if soft_checks:
        header = ("ONE THING TO CHECK" if len(soft_checks) == 1
                   else f"{len(soft_checks)} THINGS TO CHECK")
        lines.append(header)
        lines.append("")
        for index, (code, attribute, bucket) in enumerate(soft_checks, start=1):
            lines.extend(_render_one(index, code, attribute, bucket, parent_blocked_types))
            lines.append("")

    lines.append("TO UPLOAD")
    lines.append("")

    # A workbook whose sample type was hard-rejected is not something to
    # upload -- the message above just said it is blocked.
    #
    # A HARD_REJECT sample type whose own workbook creates new samples (i.e.
    # it is not itself a backfill) blocks every backfill in this run too: a
    # backfill's rows reference the samples that workbook would have created,
    # and those rows describe samples that do not exist until it lands. This
    # is detected from the artifact side (whether the *blocked* type's own
    # workbook is a "_update" one), not from sample-type naming, so it holds
    # for any [NEW] sample type, not one particular prefix convention.
    blocked_children = any(
        built.disposition == qa.HARD_REJECT
        and not _is_backfill(_resolve_artifact(artifacts, sample_type)[0] or "")
        for sample_type, built in ordered
    )

    uploadable = []
    for sample_type, built in ordered:
        if built.disposition == qa.HARD_REJECT:
            continue
        key, path = _resolve_artifact(artifacts, sample_type)
        if path is None:
            continue
        uploadable.append((key, path))
    uploadable.sort(key=lambda kp: (_is_backfill(kp[0]), kp[0]))

    if uploadable:
        for order, (key, path) in enumerate(uploadable, 1):
            if _is_backfill(key) and blocked_children:
                # Still named -- the reader must not lose track of it -- but
                # not offered as a step to take now: uploading it while a
                # child workbook above is blocked would leave it describing
                # samples that were never created.
                suffix = "   — hold until the blocked workbooks above are fixed"
            elif _is_backfill(key):
                suffix = '   — tick "update existing samples"'
            else:
                suffix = "   — normal upload"
            lines.append(f"  {order}.  {path.rsplit('/', 1)[-1]}{suffix}")
    elif blocked:
        lines.append("  Nothing to upload yet — fix the blockers above first.")
    else:
        lines.append("  Nothing to upload — no workbook was produced for this run.")

    return "\n".join(lines)


def _render_one(index, code, attribute, bucket, parent_blocked_types: frozenset = frozenset()):
    count = bucket["count"]
    detail = bucket["detail"]
    sample_type = bucket.get("sample_type", "")
    rows = _plural(count)

    if code == qa.UNAPPROVED_ATTRIBUTE:
        return [
            f"  {index}.  {_name(attribute)} in {_workbook_ref(sample_type)} — worth a sanity-check.",
            "",
            f"      I filled this in on {count} sample{'' if count == 1 else 's'}"
            f" for the first time. {detail.get('example', '')}".rstrip(),
            "",
            "      That judgement is mine and nobody has confirmed it. If you know",
            "      what to expect for these samples, that is the number to look at.",
            "",
            "      Leave it as-is — the cells are marked in the workbook's Provenance",
            "      sheet, so it stays traceable.",
            "",
            "      Or ask an administrator to confirm it, and it will not be flagged",
            "      again on this run or any future one.",
        ]
    if code == qa.ATTRIBUTE_NOT_DEFINED:
        return [
            f"  {index}.  {_name(attribute)} in {_workbook_ref(sample_type)} has nowhere to live.",
            "",
            "      There is no matching sample attribute, so I have written it into",
            f"      the Notes of {count} sample{'' if count == 1 else 's'}, tagged with this run.",
            "",
            "      I have told the administrators, who can add it as a real attribute.",
            "      Re-run this reingest once they have, and I will file it properly.",
            "      Existing Notes content is preserved — nothing was overwritten.",
        ]
    if code == qa.UNRESOLVED_UID:
        samples = [s for s in bucket.get("samples", []) if s]
        if not samples:
            single = detail.get("nfcore_sample")
            if single:
                samples = [single]
        shown = samples[:_MAX_SAMPLES_LISTED]
        extra = len(samples) - len(shown)
        body = [
            f"  {index}.  {count} sample{'' if count == 1 else 's'} in {_workbook_ref(sample_type)} could not be matched",
            "      to any sequencing sample in NExtSEEK:",
        ]
        if shown:
            body.append("")
            body.extend(f"          {s}" for s in shown)
            if extra > 0:
                body.append(f"          ...and {extra} more")
        body.extend([
            "",
            "      Most likely they were added to the run by hand after launch.",
            "      Register them first, or tell me to skip them and build the",
            "      backfill for the ones that matched.",
        ])
        return body
    if code == qa.MULTIRUN_NOT_ATTRIBUTABLE:
        return [
            f"  {index}.  {count} sample{'' if count == 1 else 's'} in {_workbook_ref(sample_type)} were sequenced across",
            "      several runs and merged before QC.",
            "",
            "      The pipeline reports one figure for the merged result, and there",
            "      is no honest way to split it across the original samples, so they",
            "      are left out of the backfill. Their analysis outputs are unaffected.",
        ]
    if code == qa.CATALOG_REQUIRED_MISSING:
        return [
            f"  {index}.  {_name(attribute)} is missing on {count} {rows}"
            f" in {_workbook_ref(sample_type)}.",
            "",
            "      The server will accept these rows without it -- this is a curation",
            "      expectation, not something it would reject the upload over.",
            "",
            "      Fill it in if you have it, or upload as-is.",
        ]
    if code == qa.MISSING_REQUIRED:
        # A group label (e.g. "File_PrimaryData or Link_PrimaryData") is
        # already a composed phrase naming interchangeable-as-DATA
        # attributes -- not a single attribute title -- so it skips `_name`'s
        # friendly-name lookup/capitalisation and gets its own
        # alternatives-aware wording: "is required" reads oddly as a single
        # verb over several named options, and "fill it in" has no clear
        # antecedent when any one of them would do. The wording below says
        # "none of them" rather than "neither" so it stays correct however
        # many members the group has -- today's one group happens to have
        # two, but nothing here assumes exactly two.
        #
        # This branch is always HARD now: the group's SOFT sub-cases
        # (PRIMARY_DATA_LINK_ONLY, PRIMARY_DATA_UNNAMED) have their own codes
        # precisely so a bucket keyed on (code, attribute) can never mix a
        # SOFT finding into this one -- see reingest_qa.py's code constants.
        if qa.is_group_label(attribute):
            # Name the primary as the preferred answer rather than presenting
            # every member as an equally good choice: a scientist who fills
            # in only the secondary (e.g. Link_PrimaryData) would get the
            # workbook back next run carrying the new PRIMARY_DATA_LINK_ONLY
            # SOFT flag saying the server may reject it anyway -- steering
            # the reader toward the member the directional design exists to
            # treat as not provably sufficient is a wasted round trip.
            members = qa.group_members_for_label(attribute)
            # `group_label()`/`is_group_label()` guarantee `attribute` names a
            # real ALTERNATIVE_REQUIRED_GROUPS group, and every declared group
            # has at least one secondary (see `_AlternativeGroup`), so
            # `members` always has 2+ entries today -- but nothing here may
            # assume that stays true (the group this replaced carried the
            # same comment). A hypothetical single-member group has no
            # secondary to steer toward, so it falls back to the plain,
            # non-alternatives wording below rather than rendering "a  value
            # may be accepted too" with a blank secondary.
            if len(members) <= 1:
                return [
                    f"  {index}.  {_name(attribute)} is required and missing on {count} {rows}"
                    f" in {_workbook_ref(sample_type)}.",
                    "",
                    "      I cannot upload these rows without it. I could not derive",
                    "      the value; fill it in, or tell me where to get it.",
                ]
            primary = _name(members[0])
            secondaries = " or ".join(_name(m) for m in members[1:])
            return [
                f"  {index}.  One of {attribute} is required, and none of them is present,"
                f" on {count} {rows} in {_workbook_ref(sample_type)}.",
                "",
                f"      Fill in {primary} if you have it (a {secondaries} value may be",
                "      accepted too, but not every sample type takes it on its own), or",
                "      tell me where to get one.",
            ]
        return [
            f"  {index}.  {_name(attribute)} is required and missing on {count} {rows}"
            f" in {_workbook_ref(sample_type)}.",
            "",
            "      I cannot upload these rows without it. I could not derive",
            "      the value; fill it in, or tell me where to get it.",
        ]
    if code == qa.PRIMARY_DATA_LINK_ONLY:
        # The group is directional (see reingest_qa.py's
        # ALTERNATIVE_REQUIRED_GROUPS comment): a secondary member (here, a
        # link) was supplied, and the row can still be named (Name is
        # present), but only the primary member's presence is ever provably
        # enough -- SEEK requires the primary on some sample types and never
        # requires the secondary on any of them. So this batch may or may not
        # be rejected at upload; a human decides, it doesn't block.
        #
        # `detail` is always populated by reingest_qa.qa_rows for this code,
        # but a hand-built Finding (tests, or a future caller) could omit it
        # -- `or attribute` keeps that harmless (a named group-label phrase)
        # instead of rendering "give  but not ." with both names blank.
        primary = _name(detail.get("primary") or attribute)
        secondary = _name(detail.get("present_secondary") or attribute)
        return [
            f"  {index}.  {count} {rows} in {_workbook_ref(sample_type)}"
            f" {_verb(count, 'give')} {secondary} but not {primary}.",
            "",
            f"      {secondary} and {primary} can point at the same data, but not",
            f"      every sample type accepts {secondary} on its own -- the server",
            f"      may still require {primary} here and reject the row without it.",
            "",
            f"      Add {primary} if you have it, or leave it as-is and let the",
            "      upload attempt settle whether this sample type needs it.",
        ]
    if code == qa.PRIMARY_DATA_UNNAMED:
        # Important 3: unlike PRIMARY_DATA_LINK_ONLY above, this sub-case is
        # not a maybe. Every SEEK upload path titles a new sample from Name,
        # falling back to File_PrimaryData (seek/sample/upload.py's per-row
        # check; seek/sample/core.py falls back further, to the literal title
        # "Undefined") -- with neither present, there is nothing left to
        # derive a title from, and the row is rejected on every sample type,
        # not just the ones that require the PrimaryData path itself.
        primary = _name(detail.get("primary") or attribute)
        secondary = _name(detail.get("present_secondary") or attribute)
        return [
            f"  {index}.  {count} {rows} in {_workbook_ref(sample_type)}"
            f" {_verb(count, 'give')} {secondary}, but neither {primary} nor a Name.",
            "",
            f"      SEEK titles a new sample from Name, or falls back to {primary} --",
            "      with neither present here, it cannot title the sample and will",
            "      reject the row, on every sample type, not just some.",
            "",
            f"      Add {primary} or a Name value, or tell me where to get one.",
        ]
    if code == qa.UNKNOWN_SAMPLETYPE:
        # Deliberate exception to the _workbook_ref convention used
        # everywhere else in this function: the subject here is the sample
        # *type* itself, not the workbook that names it. "The Z.BOGUS
        # workbook is not in NExtSEEK's catalog of sample types" is false --
        # a workbook is never a catalog entry, the type is -- so this branch
        # names the type directly instead.
        subject = sample_type if sample_type else "This sample type"
        return [
            f"  {index}.  {subject} is not in NExtSEEK's"
            " catalog of sample types.",
            "",
            "      Reingest never creates a sample type on its own. Check the code",
            "      for a typo, or ask an administrator to add it, then re-run.",
        ]
    if code == qa.BLANK_PARENT:
        return [
            f"  {index}.  {count} {rows} in {_workbook_ref(sample_type)}"
            f" {_verb(count, 'name')} no input sample.",
            "",
            "      Every reingested sample is derived from something. Tell me which",
            "      sequencing sample each of these came from, and I will fill it in.",
        ]
    if code == qa.LINEAGE_UNRESOLVED:
        # A row with no parent-ish key at all also trips MISSING_REQUIRED on
        # "Parent" now (Parent is exempt from the SEEK-required-flag split --
        # see reingest_qa._ALWAYS_HARD_REQUIRED), which blocks this sample
        # type's whole workbook. When that HARD finding is present for this
        # sample type, "upload as-is" below would be a straight
        # contradiction of the "WHAT IS BLOCKING" section above -- so this
        # branch defers to that blocking finding instead of repeating its
        # own (now false) "ships anyway" story.
        if sample_type in parent_blocked_types:
            return [
                f"  {index}.  {count} {rows} in {_workbook_ref(sample_type)}"
                f" {_verb(count, 'have', 'has')} no parent identified.",
                "",
                "      This is the missing Parent blocking this workbook above --",
                "      fixing that also resolves this; there is nothing further to do",
                "      here on its own.",
            ]
        return [
            f"  {index}.  {count} {rows} in {_workbook_ref(sample_type)}"
            f" {_verb(count, 'ship', 'ships')} with no parent identified.",
            "",
            "      I could not tell which sequencing sample produced these outputs,",
            "      so I kept them rather than dropping the files from NExtSEEK.",
            "",
            "      Upload as-is and attach the parent in NExtSEEK once you know it,",
            "      or identify the source sample now and re-run this reingest first.",
        ]
    if code == qa.PARENT_UID_NOT_FOUND:
        return [
            f"  {index}.  {count} {rows} in {_workbook_ref(sample_type)}"
            f" {_verb(count, 'name')} an input sample that does not resolve to anything in NExtSEEK.",
            "",
            "      Register that sample first, or correct the identifier, then re-run.",
        ]
    if code == qa.DUPLICATE_NAME:
        return [
            f"  {index}.  {count} {rows} in {_workbook_ref(sample_type)}"
            f" {_verb(count, 'claim')} a name already used elsewhere in this batch.",
            "",
            "      The server uses that name to detect re-uploads, so a duplicate",
            "      inside one batch is not safe. Rename one, or drop the duplicate.",
        ]
    if code == qa.SURPRISE_SENTINEL:
        what = _name(attribute) if attribute else "A value"
        return [
            f"  {index}.  {what} on {count} {rows} in {_workbook_ref(sample_type)} still contains a placeholder",
            "      marker (TODO, XXX, TBD, or similar) that was not deliberately",
            "      flagged as a placeholder.",
            "",
            "      Replace it with the real value, or confirm it is intentional and",
            "      it will not be flagged again.",
        ]
    if code == qa.UID_MISSING_IN_UPDATE:
        return [
            f"  {index}.  {count} {rows} in {_workbook_ref(sample_type)}"
            f" {_verb(count, 'name')} no existing sample to update.",
            "",
            "      I could not match these to anything already in NExtSEEK.",
            "      Identify the sample, or leave the row out of this backfill.",
        ]
    if code == qa.UID_PRESENT_IN_NEW:
        return [
            f"  {index}.  {count} {rows} in {_workbook_ref(sample_type)}"
            f" {_verb(count, 'are', 'is')} meant to create new samples but"
            f" already {_verb(count, 'carry', 'carries')} an existing",
            "      sample's identifier.",
            "",
            "      New samples and updates cannot be mixed in one workbook. Say",
            "      which was intended for these rows.",
        ]
    if code == qa.NOTES_WOULD_CLOBBER:
        reason = detail.get("reason")
        if reason == "existing Notes not fetched":
            why = [
                "      I could not read the current Notes on these samples, so I",
                "      refused to write over text I never saw.",
            ]
        else:
            # "existing text absent", or any other/unknown reason -- treat it
            # the same way: the safest assumption is that writing would lose
            # something, since that is exactly what this guard exists to catch.
            why = [
                "      The Notes I was about to write does not contain what is",
                "      already there, and writing it would destroy that text.",
            ]
        return [
            f"  {index}.  Writing Notes on {count} {rows} in {_workbook_ref(sample_type)}"
            " would destroy text someone already wrote.",
            "",
            *why,
            "",
            "      Nothing was written. Retry once that is fixed, or ask for the",
            "      note to be left alone.",
        ]
    return [
        f"  {index}.  Something on {count} {rows} in {_workbook_ref(sample_type)} needs a look before upload.",
        "",
        "      This does not have friendly wording yet. The finding is preserved",
        "      in the run's audit trail, so nothing is lost by checking there.",
        "      Check the affected rows in the workbook's Provenance sheet before",
        "      deciding whether to upload as-is or ask an administrator.",
    ]
