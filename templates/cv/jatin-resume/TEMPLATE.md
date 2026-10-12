# Template: jatin-resume

- **Type:** CV
- **Source extension:** .tex
- **Engine/toolchain:** pdflatex
- **Page limit:** 1 page
- **Fonts:** Computer Modern (LaTeX default, system/distribution font, nothing to bundle)
- **Class/packages:** article (letterpaper, 11pt); standard packages only (fullpage, titlesec, enumitem, hyperref, fontawesome5, babel, tabularx, marvosym)

## Compile command

    cd cv && pdflatex -interaction=nonstopmode <file>.tex

## Style rules

- Single column, black and white, no colour accents. ATS-friendly: `\pdfgentounicode=1` + `glyphtounicode` keep the text layer clean.
- Section order: Summary, Professional Experience, Projects, Skills, Education.
- Summary is 1-2 sentences, set in `\small`.
- **Never write the word "recent" in the Summary** (no "Recent MS graduate"), and **never put availability, start date or relocation in the Summary or anywhere else on the CV** (no "Available to start ... and willing to relocate to ..."). Those belong in the cover letter only (user's standing instruction, 2026-10-03).
- Experience and education headings use `\resumeSubheadingSingle` / `\resumeSubheading`; projects use `\resumeProjectHeading` with a GitHub link; bullets use `\resumeItem` inside `\resumeItemListStart ... \resumeItemListEnd`.
- Dates are written `Mon YYYY - Mon YYYY` with a **single ASCII hyphen** in the date argument (never `--`: LaTeX turns it into an en-dash that some applicant systems cannot parse). Verified with `pdftotext -enc UTF-8` and a byte dump.
- Skills are one block of bold category labels separated by `\\`: Languages, Frontend, Backend, Databases, Cloud \& DevOps.
- Contact line: location, phone, email, LinkedIn, GitHub, Portfolio, separated by `$|$`. Email and phone are printed as visible text.
- Reorder or trim skills and bullets per posting, but keep the section headings above.

## Known pitfalls

- Needs a one-page fit: if it spills, cut by relevance (see `05-cv-templates.md`, "Relevance-weighted cutting"). Do not shrink margins or `\vspace` to force a fit.
- Escape `\&`, `\%`, `\#`, `\$`, `\_` in content (for example `C\#`, `Cloud \& DevOps`).
- `fontawesome5` is loaded but no icons are used; if it fails to load on a given machine, remove that one line.
- Keep `\resumeItem` text inside the braces, and do not put `\vspace` between items.
- The heading rows (`\resumeSubheadingSingle`, `\resumeSubheading`, `\resumeProjectHeading`) are non-wrapping table cells: a long title or project name pushes the date off the page edge. Keep job titles, company names and project names short.
- Test compile (dummy data, pdflatex): 1 page, no errors.
- Known text-layer limit: heading rows are two-column tables, so a raw (non-layout) text extraction lists both institutions first and the dates afterwards. Dates and institutions stay in the same order, but an applicant system that pairs by position can mis-pair them. `pdftotext -layout` reads them correctly. If an ATS import mangles Education, retype it there.
