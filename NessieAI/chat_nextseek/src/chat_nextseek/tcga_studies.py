"""The cancer a question names, as the TCGA study it is stored under.

TCGA's studies are titled by their GDC project code ('ESCA', 'LIHC'), one Study per cancer type in the Investigation
'TCGA', so a question that says "esophageal" names no title the graph holds. Left to itself the graph agent matched
the word as free text, and an esophageal cohort took in bladder, biliary and testis patients whose records merely
mention the esophagus. This module maps the words people use for a cancer to its study code, for two readers:

* ``graph_context.render_tcga_studies``: the block that tells the graph agent which Study.title to scope on;
* ``helpers.query_scope``: a cancer word the query applied as its study title is applied, not a dropped keyword.

Standard library only. The codes and names are GDC's project list for TCGA (33 projects), which is fixed.
"""
from __future__ import annotations

import re
from typing import Iterable, NamedTuple


class TcgaStudy(NamedTuple):
    code: str
    name: str
    words: tuple[str, ...]  # regex stems, each matched from a word start, case-insensitive


#: Every TCGA project: its code, GDC's name, and the stems of the words a question uses for it. A stem may match
#: several studies on purpose ("lung" is LUAD and LUSC, "kidney" is KICH, KIRC and KIRP): the graph agent uses each
#: one the block lists unless the question narrows it.
TCGA_STUDIES: tuple[TcgaStudy, ...] = (
    TcgaStudy("ACC", "adrenocortical carcinoma", (r"adrenocortical", r"adrenal\s+cortex")),
    TcgaStudy("BLCA", "bladder urothelial carcinoma", (r"bladder", r"urothelial")),
    TcgaStudy("BRCA", "breast invasive carcinoma", (r"breast",)),
    TcgaStudy("CESC", "cervical squamous cell carcinoma and endocervical adenocarcinoma",
              (r"cervical\s+(?:cancer|carcinoma|tumou?r)", r"cervix", r"endocervical")),
    TcgaStudy("CHOL", "cholangiocarcinoma", (r"cholangiocarcinoma", r"bile\s+duct")),
    TcgaStudy("COAD", "colon adenocarcinoma", (r"colon\b", r"colonic", r"colorectal")),
    TcgaStudy("DLBC", "lymphoid neoplasm diffuse large B-cell lymphoma",
              (r"diffuse\s+large\s+b", r"dlbcl", r"b-cell\s+lymphoma")),
    TcgaStudy("ESCA", "esophageal carcinoma", (r"esophag", r"oesophag")),
    TcgaStudy("GBM", "glioblastoma multiforme", (r"glioblastoma", r"\bgbm\b")),
    TcgaStudy("HNSC", "head and neck squamous cell carcinoma", (r"head\s+and\s+neck", r"oral\s+cavity", r"hnscc")),
    TcgaStudy("KICH", "kidney chromophobe", (r"kidney", r"renal", r"chromophobe")),
    TcgaStudy("KIRC", "kidney renal clear cell carcinoma", (r"kidney", r"renal", r"ccrcc")),
    TcgaStudy("KIRP", "kidney renal papillary cell carcinoma", (r"kidney", r"renal")),
    TcgaStudy("LAML", "acute myeloid leukemia", (r"acute\s+myeloid", r"\baml\b", r"leuka?emi")),
    TcgaStudy("LGG", "brain lower grade glioma", (r"lower[\s-]+grade\s+glioma", r"low[\s-]+grade\s+glioma")),
    TcgaStudy("LIHC", "liver hepatocellular carcinoma", (r"liver", r"hepatocellular", r"\bhcc\b")),
    TcgaStudy("LUAD", "lung adenocarcinoma", (r"lung",)),
    TcgaStudy("LUSC", "lung squamous cell carcinoma", (r"lung",)),
    TcgaStudy("MESO", "mesothelioma", (r"mesothelioma",)),
    TcgaStudy("OV", "ovarian serous cystadenocarcinoma", (r"ovar(?:y|ian)",)),
    TcgaStudy("PAAD", "pancreatic adenocarcinoma", (r"pancrea",)),
    TcgaStudy("PCPG", "pheochromocytoma and paraganglioma", (r"pheochromocytoma", r"paraganglioma")),
    TcgaStudy("PRAD", "prostate adenocarcinoma", (r"prostat",)),
    TcgaStudy("READ", "rectum adenocarcinoma", (r"rect(?:um|al)\b", r"colorectal")),
    TcgaStudy("SARC", "sarcoma", (r"sarcoma",)),
    TcgaStudy("SKCM", "skin cutaneous melanoma", (r"cutaneous\s+melanoma", r"skin\s+(?:cancer|melanoma)",
                                                  r"(?<!uveal )melanoma")),
    TcgaStudy("STAD", "stomach adenocarcinoma", (r"stomach", r"gastric")),
    TcgaStudy("TGCT", "testicular germ cell tumors", (r"testic", r"testis", r"germ\s+cell")),
    TcgaStudy("THCA", "thyroid carcinoma", (r"thyroid",)),
    TcgaStudy("THYM", "thymoma", (r"thym(?:oma|us|ic)",)),
    TcgaStudy("UCEC", "uterine corpus endometrial carcinoma", (r"endometri(?:al|um)\s+(?:cancer|carcinoma)",
                                                                r"uter(?:ine|us)\s+corpus", r"\bucec\b")),
    TcgaStudy("UCS", "uterine carcinosarcoma", (r"carcinosarcoma",)),
    TcgaStudy("UVM", "uveal melanoma", (r"uveal",)),
)

BY_CODE = {study.code: study for study in TCGA_STUDIES}

#: The question names TCGA: the block is sent only then, so "liver tumours" in a mouse study is never steered to
#: TCGA's liver cohort.
TCGA_NAMED_RE = re.compile(r"\btcga\b|\bcancer\s+genome\s+atlas\b", re.IGNORECASE)

_COMPILED = tuple((study, re.compile(r"\b(?:" + "|".join(study.words) + r")", re.IGNORECASE))
                  for study in TCGA_STUDIES)


class NamedStudy(NamedTuple):
    word: str  # the question's own word, as written
    code: str
    name: str


def studies_named(text: str | None, codes: Iterable[str] | None = None) -> list[NamedStudy]:
    """Each TCGA study a cancer word in ``text`` names, in study-code order, with the word as written.

    ``codes``, when given, keeps only those studies (the Study titles this box holds), so a box without TCGA is sent
    nothing. A word naming several studies gives one entry per study.
    """
    keep = None if codes is None else {str(c) for c in codes}
    hits: list[NamedStudy] = []
    for study, pattern in _COMPILED:
        if keep is not None and study.code not in keep:
            continue
        m = pattern.search(text or "")
        if m:
            hits.append(NamedStudy(_whole_word(text or "", m.start(), m.end()), study.code, study.name))
    return hits


#: Words that say a cancer is meant without narrowing which: "esophageal tumors" is the esophageal study.
_GENERIC_WORDS = frozenset({"cancer", "cancers", "tumor", "tumors", "tumour", "tumours", "carcinoma", "carcinomas",
                            "neoplasm", "neoplasms", "patient", "patients", "sample", "samples", "cohort", "tcga",
                            "the", "and", "our"})


def word_names_study(word: str | None, code: str) -> bool:
    """``word`` (a keyword the entity step resolved) names the TCGA study ``code`` and nothing narrower:
    "esophageal" and 'ESCA', "lung" and 'LUSC', "lung adenocarcinoma" and 'LUAD'.

    Every word of the keyword (generic ones aside) must be one the study is known by: matched by one of its stems or
    a word of its GDC name. So "lung adenocarcinoma" does not name 'LUSC', and "esophageal squamous" does not name the
    whole of 'ESCA', which also holds adenocarcinomas: a study scope has not applied the narrower word.
    """
    study = BY_CODE.get(str(code or "").strip().upper())
    text = str(word or "").strip()
    if study is None or not text:
        return False
    pattern = next(p for s, p in _COMPILED if s.code == study.code)
    if not pattern.search(text):
        return False
    name_words = set(re.findall(r"[a-z0-9]+", study.name.lower()))
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        if len(token) < 3 or token in _GENERIC_WORDS or token in name_words or pattern.match(token):
            continue
        return False
    return True


def _whole_word(text: str, start: int, end: int) -> str:
    """The whole word (or phrase) a stem matched, extended to the next non-letter: "esophag" -> "esophageal"."""
    while end < len(text) and (text[end].isalnum() or text[end] in "-'"):
        end += 1
    return text[start:end].rstrip("'-")
