# Document Extractor

Local extraction pipeline for the Research agent hub. Converts documents that
the `researcher` agent downloaded into `/Users/samalmutairi/ws/Research agent/inbox/files/`
into markdown sidecar files, so the `extractor` agent (defined in
`~/.cursor/agents/extractor.md`) can answer questions from them without ever
loading a full document into model context.

## Role in the pipeline

| Agent | Job |
|---|---|
| Agent X (any project) | Asks questions. Does not search, download, or read PDFs. |
| Researcher | Web search only. Downloads PDFs to the hub. Never opens them. |
| **Document Extractor** (this) | Reads hub files on disk → markdown/text. Never searches or downloads. |

## Usage

```bash
"/Users/samalmutairi/ws/Document Extractor/scripts/extract-doc" "<absolute-path>" [more paths...]
```

- Works from any caller cwd; absolute paths are required.
- Writes `<file>.extract.md` next to each source (PDF pages are anchored with
  `<!-- page N -->` markers for page-level citations).
- Prints a compact report to stdout: status, page/word counts, and a heading
  outline — everything the agent needs to grep the sidecar for relevant
  sections instead of reading it whole.
- Re-runs are cached: if a sidecar is newer than its source, it is reused
  (`--force` to re-extract).

Statuses: `ok`, `ok_partial` (some pages need OCR), `scanned_needs_ocr`,
`empty`, `corrupt`, `unsupported`, `missing`. Anything other than `ok` /
`ok_partial` means the content could not be (fully) extracted — the agent
reports that in **Gaps** instead of inventing content.

## Engines

- **PDF:** [pdf-inspector](https://github.com/firecrawl/pdf-inspector) — Rust
  library that classifies pages (text-based vs scanned) and emits clean
  markdown with reading order and tables preserved. No OCR: scanned pages are
  flagged, not hallucinated. [markitdown](https://github.com/microsoft/markitdown)
  is the fallback if pdf-inspector rejects a file.
- **Everything else** (docx, pptx, xlsx, html, csv, epub, ...): markitdown.
- **Plain text** (txt/md): copied through as-is.

## Setup

`scripts/extract-doc` bootstraps its own `.venv` from `requirements.txt` on
first run. To set up manually:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```
