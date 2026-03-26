---
name: review-epic
description: Review a completed epic's implementation for quality, simplicity, and alignment with project context. Use after finishing an epic or major implementation milestone.
disable-model-invocation: true
---

# Epic Implementation Review

You are reviewing a just-completed epic in a Turkish legal jurisprudence RAG system. Run a thorough but concise review across three dimensions.

## Step 1: Identify What Changed

!`git diff --stat main 2>/dev/null || git diff --stat HEAD~10 2>/dev/null || echo "No git history — list all project files manually"`

!`git diff --name-only main 2>/dev/null || git diff --name-only HEAD~10 2>/dev/null`

Read every changed/new file fully before reviewing.

## Step 2: Context Alignment

Check that the implementation is consistent with the project's guiding documents:

1. **Implementation plan** — Read `docs/implementation-plan.md`. Does the completed work match the epic's acceptance criteria? Are there gaps or scope creep?
2. **Research foundations** — Read `docs/research.md`. Are design decisions grounded in the SOTA research (e.g., evaluation metrics, chunking strategies, retrieval approaches)?
3. **Turkish law model** — Read `docs/turkish-law-reference.md`. Does the implementation correctly model Turkish court hierarchy, law branches, and legal concepts?
4. **Existing artifacts** — If the epic produced validation scripts, schemas, or test suites, run them and report failures.

For each misalignment found, report:
- **What**: The specific discrepancy
- **Where**: File and line
- **Impact**: Does this break something downstream or is it cosmetic?

## Step 3: Code Quality Review

For every file with code (Python, JSON schemas, shell scripts):

### Simplicity
- Is there any abstraction that's only used once? Flag it.
- Are there config files, CLI argument parsers, or class hierarchies that a plain function would replace?
- Could any file be deleted entirely without losing capability?

### Correctness
- Do hardcoded references (doc_ids, filenames, court names) actually exist in the corpus?
- Are there off-by-one errors, missing edge cases, or silent failures?
- Does error handling match the actual failure modes (not hypothetical ones)?

### Overengineering
- Is anything built for flexibility that the project doesn't need yet?
- Are there unused imports, dead code paths, or feature flags?
- Is there a simpler data structure that would work (e.g., list vs. dict, flat vs. nested)?
- Are there unnecessary type annotations, docstrings, or defensive checks on internal-only code?

### Craft

Review the code the way a senior engineer reviews a junior's PR — not for whether it works, but for whether it was *considered*. The difference is in the small decisions: what was named well, what was left out, what didn't need to be said.

- **Comments** — A comment should exist only when the code cannot speak for itself. A comment that restates logic is clutter. A comment that explains *why* something non-obvious was chosen is invaluable. Flag both the missing and the redundant.
- **Naming** — Do names reveal intent at the call site, not just at the definition? A good name eliminates the need to read the implementation. A great name makes the wrong usage look obviously wrong.
- **Structure** — Does each function do one thing and does the file read top-to-bottom without forcing the reader to jump? Is related logic grouped, or scattered across files for the sake of "organization"?
- **Economy** — Is every line pulling its weight? Three clear lines are better than one clever one. But ten lines that could be three is just noise. Look for the version of this code that has nothing left to remove.
- **Conventions** — Are patterns consistent within the file and across the project? Inconsistency signals that the code was assembled, not authored.
- **Signal-to-noise** — Strip away everything that doesn't serve the reader or the runtime. No defensive checks against impossible states. No logging that nobody reads. No abstractions awaiting a future that may never arrive.

## Step 4: Output

Structure your review as:

### Context Alignment
- List misalignments or confirm "All aligned with plan/research/law model"

### Issues Found
For each issue:
- **File**: path:line
- **Category**: simplicity | correctness | overengineering | craft
- **Issue**: One sentence
- **Fix**: One sentence
- **Severity**: critical (breaks something) | warning (should fix) | nit (optional)

### Verdict
One paragraph: Is this implementation the simplest correct solution? What would you change? What's done well?

Keep the review honest and direct. Praise what deserves it, flag what doesn't.
