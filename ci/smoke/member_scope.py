"""Pure helpers for test_member_scope.py. No network, no Django."""
import json
import re

_MY_PROJECT_IDS = re.compile(r"const myProjectIds = (null|\[[^\]]*\]);")


def parse_my_project_ids(html: str):
    """The project ids the upload page offers its caller: a list of ints, or None for a superuser.

    Raises ValueError when the page carries no such constant, so a changed template fails loudly
    instead of reading as "offers nothing".
    """
    m = _MY_PROJECT_IDS.search(html)
    if not m:
        raise ValueError("no `const myProjectIds = ...;` in the upload page")
    ids = json.loads(m.group(1))
    return None if ids is None else [int(i) for i in ids]


def pick_non_member(all_ids, mine):
    """An id from `all_ids` (any type) that is not in `mine`, as an int, or None when there is none."""
    have = {int(i) for i in mine}
    for pid in all_ids:
        if int(pid) not in have:
            return int(pid)
    return None
