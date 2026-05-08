# Architecture Decision Records

Each non-obvious technical choice gets one numbered file here. Format follows
[Michael Nygard's ADR template](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions):

```
# NNNN. Short title

Status: Proposed | Accepted | Superseded by NNNN
Date:   YYYY-MM-DD

## Context
What problem are we solving? What constraints / forces matter?

## Decision
What did we choose? State it plainly.

## Consequences
What gets easier, what gets harder, what new traps appear.
```

## Rules

- Filenames: `NNNN-short-slug.md` (zero-padded 4 digits, kebab-case slug).
- Numbers are append-only. Once issued, never renumber.
- Don't edit accepted ADRs in-place. If a decision changes, write a new ADR
  with `Supersedes NNNN` in the header, and flip the old one's status to
  `Superseded by MMMM`.
- One decision per file. If a change touches three things, write three ADRs.
- Keep them short — context in 1–2 paragraphs, decision in 2–5 sentences,
  consequences as a bulleted list. If you can't, you're trying to record
  too much at once.

## Index

- [0001 — SQLAlchemy over raw pymysql](0001-sqlalchemy-over-pymysql.md)
- [0002 — Visibility over resilience in the demo app](0002-visibility-over-resilience-in-demo-app.md)
- [0003 — Layered structure for `app/`](0003-layered-structure-for-app.md)
