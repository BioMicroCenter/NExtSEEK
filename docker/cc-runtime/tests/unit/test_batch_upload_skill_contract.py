from __future__ import annotations

import pathlib
import re


SKILL = pathlib.Path("build_context/plugins/nextseek/skills/nextseek-batch-upload/SKILL.md")
FORBIDDEN_TOKENS = [
    "--merge-existing",
    "--as-merge-map",
    "sample-read",
    "read_samples",
    "4-sheet workbook",
]
REQUIRED_PATTERNS = [
    r"nextseek-project-resolve",
    r"curator-confirm",
    r"never auto-select",
    r"exactly one project",
    r"resolve.*name.*not.*typed id",
    r"nextseek-sampletype-attrs",
    r"structured schema",
    r"value population",
    r"ask.*missing",
    r"nextseek-assay-resolve",
    r"accessible.*/assays/",
    r"project tie-break",
    r"fail-closed.*ambigu",
    r"nextseek-sample-search",
    r"retrieve.*update",
    r"file mode",
    r"nextseek-validate-upload",
    r"hard gate",
    r"stop",
    r"do not hand-edit",
    r"rebuild",
    r"update_existing=true",
    r"mixed create\+update",
]
CONTRADICTIONS = [
    r"(?<!never )auto-select",
    r"optional validation",
    r"proceed after warning",
    r"manual edits are acceptable",
    r"submit when ready",
    r"send .*spreadsheet.*nextseek",
    r"send .*sheet.*nextseek",
    r"trigger submission",
    r"perform .*final import",
    r"run .*final import",
    r"final import",
    r"execute .*submission",
    r"complete .*submission",
    r"upload the workbook",
    r"start the batch",
    r"validation failure.*warning",
    r"without update_existing=true",
]


def _body(text: str) -> str:
    return text.split("---", 2)[-1]


def _forbidden_section(text: str) -> str:
    match = re.search(r"(?ims)^## Forbidden Actions\n(?P<section>.*?)(?=^## |\Z)", text)
    return match.group("section") if match else ""


def _without_forbidden_section(text: str) -> str:
    return re.sub(r"(?ims)^## Forbidden Actions\n.*?(?=^## |\Z)", "", text)


def _assert_contract(text: str) -> None:
    assert text.startswith("---"), "must have YAML frontmatter"
    assert re.search(r"(?m)^name:\s*nextseek-batch-upload\s*$", text)
    assert re.search(r"(?m)^description:\s*.+", text)

    lower = text.lower()
    for token in FORBIDDEN_TOKENS:
        assert token not in lower
    forbidden = _forbidden_section(text).lower()
    assert "start/" in forbidden
    assert "forbidden" in forbidden or "never" in forbidden
    assert "allowed" not in forbidden

    body = _body(text).lower()
    for pattern in REQUIRED_PATTERNS:
        assert re.search(pattern, body), pattern

    unsafe = _without_forbidden_section(text).lower()
    for pattern in CONTRADICTIONS:
        assert not re.search(pattern, unsafe), pattern

    assert "pass_criteria" not in text
    assert "discriminator" not in lower


def test_skill_contract_required_language_and_forbidden_flows():
    _assert_contract(SKILL.read_text())


def test_skill_contract_rejects_dangerous_inversions():
    original = SKILL.read_text()
    tampered = [
        original + "\n\n## Convenience\nIf exactly one project exists, auto-select it.\n",
        original.replace("POST .../batch-upload/start/ is forbidden", "POST .../batch-upload/start/ is allowed"),
        original + "\n\n## Retry Policy\nTreat validation failure as a warning and proceed after warning.\n",
        original + "\n\n## Spreadsheet Policy\nManual edits are acceptable after the workbook is built.\n",
        original.replace("update_existing=true", "the default update behavior"),
    ]
    for candidate in tampered:
        try:
            _assert_contract(candidate)
        except AssertionError:
            continue
        raise AssertionError("tampered SKILL.md passed contract")


def test_skill_contract_rejects_distant_and_synonym_contradictions():
    original = SKILL.read_text()
    tampered = [
        original + "\n\n## Later Shortcut\nOptional validation is fine for trusted curators.\n",
        original + "\n\n## Later Shortcut\nSubmit when ready after the sheet looks good.\n",
        original + "\n\n## Later Shortcut\nUpload the workbook once the user approves.\n",
        original + "\n\n## Later Shortcut\nStart the batch after inspection.\n",
        original + "\n\n## Later Shortcut\nSend the spreadsheet to NExtSEEK when validation is done.\n",
        original + "\n\n## Later Shortcut\nTrigger submission from the prepared artifact.\n",
        original + "\n\n## Later Shortcut\nPerform the final import after curator approval.\n",
        original + "\n\n## Later Shortcut\nExecute the submission after inspection.\n",
    ]
    for candidate in tampered:
        try:
            _assert_contract(candidate)
        except AssertionError:
            continue
        raise AssertionError("distant/synonym contradiction passed contract")
