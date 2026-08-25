#!/usr/bin/env python3
"""Convert documents to markdown sidecar files with a compact stdout report.

Designed for the Research agent hub: takes absolute paths (normally under
/Users/samalmutairi/ws/Research agent/inbox/files/), writes
`<file>.extract.md` next to each source, and prints only a short report
(status, counts, heading outline) so the calling agent never has to load the
full document into its context.

PDF engine: pdf-inspector (classification + per-page markdown, no OCR).
Other formats (docx, pptx, xlsx, html, csv, epub, ...): markitdown.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

MAX_OUTLINE_LINES = 40
PAGE_MARKER = "<!-- page {n} -->"
PAGE_MARKER_RE = re.compile(r"^<!-- page (\d+) -->$")
HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$")
# Below this many words the whole extract is considered empty.
MIN_WORDS = 5

MARKITDOWN_EXTS = {
    ".docx", ".doc", ".pptx", ".xlsx", ".xls", ".html", ".htm", ".csv",
    ".json", ".xml", ".epub", ".msg", ".rtf", ".odt",
}
PLAIN_TEXT_EXTS = {".txt", ".md", ".markdown", ".rst"}


@dataclass
class Extraction:
    status: str                      # ok | ok_partial | scanned_needs_ocr | empty | corrupt | unsupported | missing
    detail: str = ""                 # short human-readable note (classification, error, ...)
    markdown: str = ""               # sidecar body
    pages: int | None = None
    pages_needing_ocr: list[int] = field(default_factory=list)
    engine: str = ""


def sha256_short(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def word_count(text: str) -> int:
    """Words of content — page markers excluded."""
    text = re.sub(r"<!-- page \d+ -->", "", text)
    return len(text.split())


# Arabic letters and diacritics; excludes Arabic-Indic digits (0660-0669) and
# punctuation (060C, 061B, 061F) so numbers and separators are never reversed.
AR_CHARS = "\u0621-\u065f\u066e-\u06d5"
AR_WORD_RE = re.compile(rf"[{AR_CHARS}]+")
# A run: Arabic words separated by spaces. Latin, digits, and punctuation
# break the run, so JSON keys and numbers embedded in Arabic stay intact.
AR_RUN_RE = re.compile(rf"[{AR_CHARS}]+(?: +[{AR_CHARS}]+)*")
# Arabic presentation forms (ligatures/positional glyphs some extractors emit).
AR_PRESENTATION_RE = re.compile(r"[\ufb50-\ufdff\ufe70-\ufeff]+")

TA_MARBUTA = "\u0629"       # strictly word-final in correct Arabic
ALEF_LAM = "\u0627\u0644"   # definite article "al-", word-initial
LAM_ALEF = "\u0644\u0627"   # what "al-" becomes when the word is reversed
HAMZA_ALEFS = "\u0622\u0623\u0625"  # آ أ إ — almost always word-initial
# Extractors decode the lam-alef ligature glyph in logical pair order, so it
# survives run reversal flipped. A generic mid-word swap is ambiguous (logical
# "حالة" reverses to the same stream pattern as a decoded ligature), so only
# the unambiguous case is repaired: a stream word ending in lam+alef+alef is a
# logical word starting with the definite article plus an alef-initial stem
# (e.g. "الاتفاقية"), where the pair must be swapped before reversal.
LAM_ALEF_ARTICLE_RE = re.compile(rf"\u0644([{HAMZA_ALEFS}\u0627])(?=\u0627$)")


def _looks_reversed(words: list[str]) -> bool:
    """Score positional letter statistics: ta marbuta is word-final and
    hamza-alef / the definite article word-initial only when the text is in
    logical order; reversed (visual-order) text flips those positions."""
    reversed_score = correct_score = 0
    for w in words:
        if len(w) < 2:
            continue
        if w[0] == TA_MARBUTA:
            reversed_score += 1
        if w[-1] == TA_MARBUTA:
            correct_score += 1
        if w.endswith(LAM_ALEF):
            reversed_score += 1
        if w.startswith(ALEF_LAM):
            correct_score += 1
        if w[-1] in HAMZA_ALEFS:
            reversed_score += 1
        if w[0] in HAMZA_ALEFS:
            correct_score += 1
    return reversed_score > correct_score


def _reverse_run(run: str) -> str:
    words = [LAM_ALEF_ARTICLE_RE.sub(lambda m: m.group(1) + "\u0644", w) for w in run.split(" ")]
    return " ".join(words)[::-1]


def fix_arabic(text: str) -> tuple[str, bool]:
    """Repair Arabic text extracted from PDFs.

    1. Normalize presentation-form glyphs to standard codepoints (NFKC on
       just those ranges, so the rest of the document is untouched).
    2. Per line, if the Arabic reads in visual (reversed) order — decided
       from letter-position statistics over the line's Arabic words —
       reverse each Arabic run, restoring letter and word order. Lines with
       no positional evidence are left alone: one PDF can mix visual-order
       regions with correct ones, and corrupting correct text is worse than
       missing a reversed fragment.

    Returns (fixed_text, whether_anything_changed).
    """
    normalized = AR_PRESENTATION_RE.sub(
        lambda m: unicodedata.normalize("NFKC", m.group(0)), text
    )
    changed = normalized != text
    text = normalized

    fixed_lines = []
    reversed_any = False
    for line in text.split("\n"):
        words = AR_WORD_RE.findall(line)
        if words and _looks_reversed(words):
            line = AR_RUN_RE.sub(lambda m: _reverse_run(m.group(0)), line)
            reversed_any = True
        fixed_lines.append(line)
    if reversed_any:
        text = "\n".join(fixed_lines)
        changed = True
    return text, changed


def extract_pdf(path: Path) -> Extraction:
    import pdf_inspector

    try:
        detection = pdf_inspector.detect_pdf(str(path))
        result = pdf_inspector.extract_pages_markdown(str(path))
    except Exception as exc:  # invalid/corrupt PDF
        return Extraction(status="corrupt", detail=f"pdf-inspector: {exc}", engine="pdf-inspector")

    parts: list[str] = []
    for page in result.pages:
        page_no = page.page + 1  # PageMarkdown.page is 0-indexed
        parts.append(PAGE_MARKER.format(n=page_no))
        body = page.markdown.strip()
        if not body and page.needs_ocr:
            body = f"*[page {page_no}: no extractable text — {page.ocr_reason or 'needs OCR'}]*"
        parts.append(body)
    markdown = "\n\n".join(parts).strip()
    markdown, arabic_fixed = fix_arabic(markdown)

    total_pages = detection.page_count
    ocr_pages = sorted(result.pages_needing_ocr)  # 1-indexed
    words = word_count(markdown)
    detail = f"{detection.pdf_type}, confidence {detection.confidence:.2f}"
    if arabic_fixed:
        detail += "; arabic order corrected"

    if total_pages and len(ocr_pages) == total_pages:
        status = "scanned_needs_ocr"
    elif words < MIN_WORDS and ocr_pages:
        status = "scanned_needs_ocr"
    elif words < MIN_WORDS:
        status = "empty"
    elif ocr_pages:
        status = "ok_partial"
        detail += f"; pages needing OCR: {ocr_pages}"
    else:
        status = "ok"

    return Extraction(
        status=status,
        detail=detail,
        markdown=markdown,
        pages=total_pages,
        pages_needing_ocr=ocr_pages,
        engine="pdf-inspector",
    )


def extract_pdf_fallback(path: Path) -> Extraction:
    """markitdown fallback when pdf-inspector fails unexpectedly."""
    from markitdown import MarkItDown

    try:
        result = MarkItDown(enable_plugins=False).convert(str(path))
    except Exception as exc:
        return Extraction(status="corrupt", detail=f"markitdown: {exc}", engine="markitdown")
    text = (result.text_content or "").strip()
    if word_count(text) < MIN_WORDS:
        return Extraction(status="scanned_needs_ocr", detail="no extractable text (fallback)", engine="markitdown")
    text, arabic_fixed = fix_arabic(text)
    detail = "markitdown fallback (no page markers)"
    if arabic_fixed:
        detail += "; arabic order corrected"
    return Extraction(status="ok", detail=detail, markdown=text, engine="markitdown")


def extract_with_markitdown(path: Path) -> Extraction:
    from markitdown import MarkItDown

    try:
        result = MarkItDown(enable_plugins=False).convert(str(path))
    except Exception as exc:
        return Extraction(status="corrupt", detail=f"markitdown: {exc}", engine="markitdown")
    text = (result.text_content or "").strip()
    if word_count(text) < MIN_WORDS:
        return Extraction(status="empty", detail="no extractable text", engine="markitdown")
    return Extraction(status="ok", markdown=text, engine="markitdown")


def extract_plain_text(path: Path) -> Extraction:
    try:
        text = path.read_text(errors="replace").strip()
    except Exception as exc:
        return Extraction(status="corrupt", detail=str(exc), engine="copy")
    if word_count(text) < MIN_WORDS:
        return Extraction(status="empty", detail="no extractable text", engine="copy")
    return Extraction(status="ok", markdown=text, engine="copy")


def has_pdf_magic(path: Path) -> bool:
    """PDFs from fetch-source often lack an extension — sniff magic bytes."""
    try:
        with path.open("rb") as f:
            return f.read(1024).lstrip().startswith(b"%PDF-")
    except OSError:
        return False


def extract(path: Path) -> Extraction:
    ext = path.suffix.lower()
    pdf_magic = has_pdf_magic(path)
    if ext == ".pdf" or pdf_magic:
        if not pdf_magic:
            return Extraction(
                status="corrupt",
                detail="named .pdf but missing %PDF header (truncated or bad download?)",
                engine="magic-check",
            )
        result = extract_pdf(path)
        if result.status == "corrupt":
            fallback = extract_pdf_fallback(path)
            if fallback.status == "ok":
                return fallback
        return result
    if ext in PLAIN_TEXT_EXTS:
        return extract_plain_text(path)
    if ext in MARKITDOWN_EXTS:
        return extract_with_markitdown(path)
    # Unknown extension: try markitdown anyway; it handles many formats.
    result = extract_with_markitdown(path)
    if result.status == "corrupt":
        result.status = "unsupported"
        result.detail = f"unrecognized format {ext or '(no extension)'}; {result.detail}"
    return result


def build_outline(markdown: str) -> list[str]:
    """Heading outline with page numbers, for targeted grep/Read by the agent."""
    outline: list[str] = []
    current_page: int | None = None
    for line in markdown.splitlines():
        page_match = PAGE_MARKER_RE.match(line)
        if page_match:
            current_page = int(page_match.group(1))
            continue
        heading = HEADING_RE.match(line)
        if heading:
            prefix = f"p{current_page}: " if current_page else ""
            indent = "  " * (len(heading.group(1)) - 1)
            outline.append(f"{prefix}{indent}{heading.group(2).strip()[:100]}")
    return outline


def sidecar_path(source: Path) -> Path:
    return source.with_name(source.name + ".extract.md")


def write_sidecar(source: Path, result: Extraction) -> Path:
    out = sidecar_path(source)
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    front = [
        "---",
        f"source: {source}",
        f"status: {result.status}",
        f"engine: {result.engine}",
        f"extracted: {now}",
        f"source_sha256: {sha256_short(source)}",
    ]
    if result.pages is not None:
        front.append(f"pages: {result.pages}")
    front.append(f"words: {word_count(result.markdown)}")
    if result.pages_needing_ocr:
        front.append(f"pages_needing_ocr: {result.pages_needing_ocr}")
    if result.detail:
        front.append(f"detail: {result.detail}")
    front.append("---")
    out.write_text("\n".join(front) + "\n\n" + result.markdown + "\n")
    return out


def read_cached_status(sidecar: Path) -> tuple[str, str]:
    status, detail = "ok", ""
    try:
        for line in sidecar.read_text().splitlines()[:12]:
            if line.startswith("status: "):
                status = line[len("status: "):].strip()
            elif line.startswith("detail: "):
                detail = line[len("detail: "):].strip()
            elif line == "---" and status != "ok":
                break
    except Exception:
        pass
    return status, detail


def report(source: Path, result: Extraction, sidecar: Path | None, cached: bool) -> None:
    print(f"== {source}")
    detail = f" ({result.detail})" if result.detail else ""
    cache_note = " [cached]" if cached else ""
    print(f"status: {result.status}{detail}{cache_note}")
    if sidecar is None or not result.markdown:
        return
    size_kb = sidecar.stat().st_size / 1024
    counts = f"{word_count(result.markdown)} words, {size_kb:.0f} KB"
    if result.pages:
        counts = f"{result.pages} pages, " + counts
    print(f"sidecar: {sidecar} ({counts})")
    outline = build_outline(result.markdown)
    if outline:
        shown = outline[:MAX_OUTLINE_LINES]
        print("outline:")
        for entry in shown:
            print(f"  {entry}")
        if len(outline) > len(shown):
            print(f"  ... {len(outline) - len(shown)} more headings (grep the sidecar)")
    else:
        print("outline: (no headings detected; grep the sidecar by keyword)")


def process(path_str: str, force: bool) -> str:
    path = Path(path_str)
    if not path.is_absolute():
        print(f"== {path_str}")
        print("status: error (absolute paths only — this tool never assumes a caller cwd)")
        return "error"
    if not path.is_file():
        print(f"== {path}")
        print("status: missing (no such file)")
        return "missing"

    out = sidecar_path(path)
    if not force and out.is_file() and out.stat().st_mtime >= path.stat().st_mtime:
        status, detail = read_cached_status(out)
        body = out.read_text().split("---", 2)[-1].strip()
        cached_result = Extraction(status=status, detail=detail, markdown=body)
        # Recover page count from the last page marker, if any.
        markers = [m.group(1) for line in body.splitlines() if (m := PAGE_MARKER_RE.match(line))]
        if markers:
            cached_result.pages = int(markers[-1])
        report(path, cached_result, out, cached=True)
        return status

    result = extract(path)
    sidecar = write_sidecar(path, result) if result.markdown else None
    report(path, result, sidecar, cached=False)
    return result.status


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="extract-doc",
        description="Convert documents to markdown sidecars (<file>.extract.md) and print a compact report.",
    )
    parser.add_argument("paths", nargs="+", help="absolute path(s) to document(s)")
    parser.add_argument("--force", action="store_true", help="re-extract even if a fresh sidecar exists")
    args = parser.parse_args()

    statuses = []
    for i, path in enumerate(args.paths):
        if i:
            print()
        statuses.append(process(path, force=args.force))
    return 2 if any(s in ("error", "missing") for s in statuses) else 0


if __name__ == "__main__":
    sys.exit(main())
