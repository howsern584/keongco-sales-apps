---
name: project-review
description: Review recent changes against THIS project's hard rules and cleanliness bar — not just generic style. Use after implementing anything, or when the user asks for a code/guardrail review. Checks the CLAUDE.md Sage-integration rules plus dead code / debug cruft / duplication, and reports findings without auto-applying fixes.
---

# Project guardrail review

Review the current changes (the working `git diff`, or the files the user names)
against this project's specific rules. This is a data-integrity and handoff review,
not a generic linter.

## 1. Hard rules from CLAUDE.md — highest priority
Flag any violation:
- **No direct writes to Sage 300 tables.** Only the official Sage Web API (OData/REST)
  or the .NET SDK. Direct DB writes are forbidden.
- **No fabricated** API endpoints, table names, field names, or capabilities. If
  something looks invented or unverified, flag it as a blocker.
- **Orders must be CONFIRMED before Sage:** the flow is
  `draft → confirmed (submitted) → pushed-to-sage`, with amend = reopen → edit →
  re-confirm → re-push. A raw draft must never reach Sage. (There is NO admin
  approval step — the salesperson confirms and pushes their own order.)
- **No auto-push.** Pushing to Sage is always a deliberate human click by the order's
  owner; amending an already-pushed order requires an explicit re-push to re-sync.
- **Customer data stays in-house** — nothing routed through external third-party
  services without the user's explicit say-so.
- **Sage references stored:** anything mapping to Sage keeps its code
  (`sage_customer_code`, `sage_item_code`, `sage_order_ref`, `sage_invoice_no`),
  not just an internal id.
- **Roles / ownership:** a salesperson confirms and pushes their OWN orders (there is no
  approval step). Invoicing/admin may **amend any order on a rep's behalf** (reopen → edit →
  re-push) and **mark an order invoiced** (which locks it) — the order stays owned by the rep
  and the amendment is audit-tracked (`amended_by`/`amended_at`). Flag anything that lets a
  plain salesperson act on *another* rep's order, or that changes an order's owner.

## 2. Cleanliness — for the incoming ERP-integration engineer
- Dead / unreachable code, unused imports, leftover debug `print()`s.
- Duplication, unclear names, stale or missing comments.
- Consistency with the surrounding code's conventions (this codebase is commented
  in plain language — keep that).

## 3. Constraints while reviewing
- Do NOT change the frontend (templates) or alter the logic/behaviour of a design
  just to "clean" it — surface those as suggestions and let the user choose depth.
- Keep explanations in plain language (the user is a capable beginner, not a developer).
- This pairs with the standing rule to self-review after every change.

## Output
Report findings grouped by severity — **Blocker** (hard-rule violation / data-integrity
risk), **Should-fix**, **Nice-to-have** — each with a `file:line` reference and a
recommended fix. Do NOT apply fixes automatically unless the user asks; surface the
concerns first.
