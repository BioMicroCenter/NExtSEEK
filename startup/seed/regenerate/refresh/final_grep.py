"""Final proof on the shipped bytes: count each needle (<work schema>.needles, case-insensitive) in each seed file.
Usage: final_grep.py OUT.json FILE.gz...   (needle text is never printed; hard needles show as #n unless they are TARGETS titles)
"""
import gzip
import json
import re
import sys

import seedlib as s

import os

needles = s.rows(f"SELECT needle, kind FROM {s.WORK}.needles ORDER BY kind, needle")
TITLES = {t for t in os.environ.get("TARGETS", "").split(",") if t}
pat = re.compile("|".join(f"(?P<n{i}>{f'(?<![A-Za-z]){n}(?![A-Za-z])' if s.is_word_needle(n) else re.escape(n)})"
                          for i, (n, _) in enumerate(needles)), re.I)
res = {}
for path in sys.argv[2:]:
    counts = [0] * len(needles)
    with gzip.open(path, "rt", encoding="utf8", errors="replace") as f:
        for line in f:
            for m in pat.finditer(line):
                counts[int(m.lastgroup[1:])] += 1
    res[path] = {f"{kind}#{i}" if n not in TITLES else n: c
                 for i, ((n, kind), c) in enumerate(zip(needles, counts)) if c}
json.dump(res, open(sys.argv[1], "w"), indent=1)
for path, hits in res.items():
    hard = {k: v for k, v in hits.items() if not k.startswith("info#")}
    print(path.rsplit("/", 1)[-1], "hard hits:", hard or 0, "| info hits:", sum(v for k, v in hits.items() if k.startswith("info#")))
