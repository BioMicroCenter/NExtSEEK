"""Refuse a workbook that inflates far past its upload size, before anything parses it (SEC-H12)."""
from __future__ import annotations

import copy
import zipfile

# Real workbooks here inflate to under 3.5 MB per member at a ratio under 13. These leave a wide margin.
MAX_MEMBER_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_RATIO = 100
_RATIO_FLOOR = 10 * 1024 * 1024  # a tiny file may compress well; only judge the ratio on a big inflation
_CHUNK = 1024 * 1024


class XlsxExpansionError(ValueError):
    """The workbook inflates past the caps (or cannot be measured)."""


def _refuse():
    return XlsxExpansionError("The workbook expands to too large a size and was refused.")


def _upload_size(fileobj) -> int:
    size = getattr(fileobj, "size", None)
    if isinstance(size, int):
        return size
    pos = fileobj.tell()
    fileobj.seek(0, 2)
    end = fileobj.tell()
    fileobj.seek(pos)
    return end


def check_xlsx_expansion(fileobj) -> None:
    """Raise XlsxExpansionError when the workbook really inflates past the caps. A non-zip passes (the parser refuses it).

    The sizes in the zip directory are the uploader's word and a parser may not honour them, so each member is
    inflated for real, in chunks, and counted; it stops at the cap. Only stored and deflate members are accepted.
    """
    pos = fileobj.tell()
    try:
        try:
            zf = zipfile.ZipFile(fileobj)
        except (zipfile.BadZipFile, OSError):
            return  # not a readable zip: nothing can inflate, and the parser refuses it with its own message
        upload_size = max(_upload_size(fileobj), 1)
        total = 0
        for info in zf.infolist():
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise _refuse()
            probe = copy.copy(info)
            probe.file_size = MAX_MEMBER_BYTES + 1  # read past the declared size, bounded by the cap
            member = 0
            try:
                with zf.open(probe) as src:
                    while chunk := src.read(_CHUNK):
                        member += len(chunk)
                        total += len(chunk)
                        if member > MAX_MEMBER_BYTES or total > MAX_TOTAL_BYTES:
                            raise _refuse()
            except XlsxExpansionError:
                raise
            except Exception as exc:  # noqa: BLE001 (any zip or codec error: refuse, never a 500)
                raise _refuse() from exc
        if total > _RATIO_FLOOR and total / upload_size > MAX_RATIO:
            raise _refuse()
    finally:
        fileobj.seek(pos)
