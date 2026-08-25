# Document Extractor

This workspace converts documents already on disk into markdown sidecars. It
never searches the web and never downloads.

- Entry point: `scripts/extract-doc "<absolute-path>" [more paths...]` — works
  from any caller cwd, writes `<file>.extract.md` next to each source, prints
  a compact report (status, counts, heading outline with page numbers).
- Answer questions **only from the extracts**; grep sidecars for keywords and
  read narrow ranges instead of whole files (PDF sidecars carry
  `<!-- page N -->` markers for page citations).
- Report bad statuses honestly (`scanned_needs_ocr`, `empty`, `corrupt`,
  `unsupported`, `missing`): they go in Gaps, never papered over with invented
  content.
- Companion repo: the Research agent hub (downloads files into its
  `inbox/files/` and dispatches this extractor). See README.md for engines
  and setup.
