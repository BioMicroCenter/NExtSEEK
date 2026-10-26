"""Refuse a workbook that inflates far past its upload size, before anything parses it (SEC-H12)."""
from __future__ import annotations

import zipfile

# Real workbooks here inflate to under 3 MB per member at a ratio under 13. These leave a wide margin.
MAX_MEMBER_BYTES = 100 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_RATIO = 100
_RATIO_FLOOR = 10 * 1024 * 1024  # a tiny file may compress well; only judge the ratio on a big inflation


class XlsxExpansionError(ValueError):
    """The workbook is not a zip, or inflates past the caps."""


def check_xlsx_expansion(fileobj) -> None:
    """Read the zip directory only (nothing is inflated) and raise XlsxExpansionError past the caps. A non-zip passes."""
    pos = fileobj.tell() if hasattr(fileobj, "tell") else 0
    try:
        infos = zipfile.ZipFile(fileobj).infolist()
    except (zipfile.BadZipFile, OSError):
        return  # not a readable zip: nothing can inflate, and the parser refuses it with its own message
    finally:
        fileobj.seek(pos)
    total = sum(i.file_size for i in infos)
    packed = sum(i.compress_size for i in infos) or 1
    if (max((i.file_size for i in infos), default=0) > MAX_MEMBER_BYTES or total > MAX_TOTAL_BYTES
            or (total > _RATIO_FLOOR and total / packed > MAX_RATIO)):
        raise XlsxExpansionError("The workbook expands to too large a size and was refused.")
