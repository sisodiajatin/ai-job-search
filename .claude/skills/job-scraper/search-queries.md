# Search Queries for Job Scraper

<!-- SETUP: Customize these queries based on your skills, target roles, and location -->

## Installed portal CLIs (primary for `/scrape`)

`/scrape` discovers every portal skill under `.agents/skills/*/SKILL.md` and runs its CLI first. Shipped country-agnostic CLIs include `linkedin-search` and `freehire-search`; Danish demos and any skill you add with `/add-portal` are included the same way. You do **not** need a matching `site:` line below for those CLIs to run.

The `site:` query templates in this file are the **WebSearch fallback** — for portals without a CLI, company career pages, or when a CLI fails.

**Language scope:** write every query category in every language listed in your CLAUDE.md Languages table (typically 1-2, sometimes more). A posting requiring a language you have *not* declared, as a job condition, is excluded before scoring; a posting requiring a *higher level* than you declared in a language you *do* work in is flagged for your own judgment, not excluded — see `04-job-evaluation.md`'s Language Gate, the single source of truth for this rule. Translate each category's keywords rather than machine-translating word-for-word (e.g. "Frontend Developer" -> "Desarrollador Frontend", not a literal word-for-word translation) if you work in more than one language.

## Search Sites

Primary (your market's job boards - scaffold one with `/add-portal`):
- **indeed.com** - largest general US job board
- **linkedin.com/jobs** - LinkedIn job listings (filter: USA, nationwide); also covered by `linkedin-search` CLI
- **wellfound.com** - startup-focused job board (matches the stated startup preference; optional to scaffold via `/add-portal`)
- **freehire.com** - covered by `freehire-search` CLI, no `site:` line needed

Secondary (company career pages via Google):
- Direct Google searches with `site:` filters for known target companies

## Query Categories

Queries are grouped by priority. Candidate works only in English, so no query translation is needed. Since there is no location constraint (open to relocation and remote, nationwide US), omit city-level filters and instead favor "remote" and broad national searches.

### Priority 1: Software Engineer / Full-Stack Developer (entry-level, startups)

These match the primary and most desired career direction.

```
site:indeed.com "software engineer" entry level startup
site:indeed.com "full-stack developer" junior OR "new grad"
site:linkedin.com/jobs "software engineer" "entry level" remote
site:wellfound.com "software engineer" OR "full-stack developer"
```

### Priority 2: Front-End Developer (entry-level, startups)

```
site:indeed.com "front end developer" junior OR entry level
site:linkedin.com/jobs "front end developer" React "entry level"
site:wellfound.com "front end developer" OR "frontend engineer"
```

### Priority 3: Software Engineering Internships

Candidate is open to internships as well as full-time roles.

```
site:indeed.com "software engineering intern" 2026
site:linkedin.com/jobs "software engineer intern" startup
site:wellfound.com internship "software engineer"
```

### Priority 4: Broader Technical (wider net)

```
site:indeed.com "junior developer" OR "associate software engineer" startup
site:linkedin.com/jobs "software developer" "entry level" remote
site:indeed.com "backend developer" junior startup
```

## Location Filter

**No location constraint for this candidate** — open to relocation and remote roles anywhere in the US. When evaluating results, do not filter out postings for commute distance; only flag postings that require relocation *outside* the US (work-authorization dependent — see the Eligibility Gate in `04-job-evaluation.md`).
- Remote (any US timezone): ideal
- Any US city/state, on-site or hybrid: acceptable
- Outside the US: flag — confirm work authorization before proceeding

## Language Filter

Your working languages and levels are in CLAUDE.md's Languages table. When filtering scraped results, apply `04-job-evaluation.md`'s Language Gate: a posting requiring a language you haven't declared at all is excluded; a posting requiring a higher level than you declared in a language you do work in is not excluded, flag it clearly instead (see `job-scraper/SKILL.md`'s Step 3 "Quick Fit Assessment" for how the flag surfaces in `/scrape` output). Postings simply *written* in a language you don't work in, that don't require it on the job, are fine.

## Date Filter

Only include jobs posted within the last 14 days, or with an application deadline that has not yet passed. If a posting date cannot be determined, include it but flag as "date unknown".

## Adapting Queries

If the user specifies a focus area, select queries from the matching category and also generate 2-3 custom queries for that focus. For example:
- "/scrape [focus_area]" -> relevant category queries + custom focus-specific queries
