---
name: implement-epic
description: Implement an epic from the implementation plan. Reads project context, builds a step-by-step approach, then executes. Use when starting a new epic or continuing one.
disable-model-invocation: true
---

# Epic Implementation

You are implementing an epic in a Turkish legal jurisprudence RAG system. Work methodically — understand before you build, build before you verify.

## Step 1: Load Context

Read these documents in full before writing any code:

1. **Implementation plan** — Read `docs/implementation-plan.md`. Find the epic the user specified. Understand its acceptance criteria, dependencies, and where it fits in the tier structure.
2. **Research foundations** — Read `docs/research.md`. Identify which research findings inform this epic's design decisions.
3. **Turkish law model** — Read `docs/turkish-law-reference.md`. Understand the court hierarchy, law branches, and legal concepts relevant to this epic.
4. **Prior work** — Review what already exists in the project. Read files that this epic depends on or extends. Do not rebuild what's already built.

## Step 2: Plan the Approach

Before touching any code, present a clear plan to the user:

- **Scope** — What this epic produces (files, scripts, data) and what it explicitly does not.
- **Steps** — Numbered implementation order. Each step should be independently verifiable.
- **Decisions** — Any design choices where multiple reasonable approaches exist. State what you'd choose and why. Ask the user if the choice is non-obvious.
- **Dependencies** — What must exist before this epic can start. Confirm it exists.

Wait for user confirmation before proceeding. Do not implement speculatively.

## Step 3: Implement

For each step in the plan:

1. Write the code.
2. Run it immediately — do not accumulate unverified steps.
3. If a step produces output (JSON, logs, metrics), inspect the output before moving on.
4. If something breaks, fix it before continuing. Do not leave broken intermediate state.

### Craft Standards

Hold yourself to the standard of a senior engineer, not a first draft:

- **Economy** — Write the minimum code that solves the problem correctly. Three clear lines over one clever one. But ten lines that could be three is noise. If a function, class, or file doesn't earn its existence, don't create it.
- **Naming** — Names should reveal intent at the call site. The reader should not need to open the definition to understand the usage.
- **Comments** — Only where the code cannot speak for itself. Explain *why*, never *what*. No commented-out code. No TODO placeholders.
- **Structure** — Each file should read top-to-bottom. Related logic stays together. Do not split into multiple files for the sake of organization — split only when a file has genuinely distinct responsibilities.
- **Consistency** — Match the patterns already established in the project. If existing code uses plain dicts, don't introduce dataclasses. If existing scripts are standalone, don't add a shared utils module.
- **No speculation** — Do not build for hypothetical future requirements. No feature flags, no plugin architectures, no config files for things with one value. Build exactly what the acceptance criteria demand.

## Step 4: Verify

After all steps are complete:

1. **Run validation** — If the epic has validation scripts or tests, run them. All must pass.
2. **Check acceptance criteria** — Go back to the implementation plan. Walk through each criterion and confirm it's met.
3. **Spot-check outputs** — If the epic produced data (JSON, manifests, datasets), manually inspect 5-10 entries for correctness.
4. **Report** — Tell the user what was built, what passed, and what remains for human review (if anything).
