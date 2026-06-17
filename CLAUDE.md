# CLAUDE.md

Behavioral guidelines for working in this repository.

These instructions bias toward careful, inspectable work over speed. For trivial tasks, use judgment.

---

## 1. Think Before Coding

Do not assume. Do not hide confusion. Surface tradeoffs.

Before implementing non-trivial changes

 State your assumptions.
 Name any ambiguity.
 Present meaningful tradeoffs briefly.
 Prefer the simplest viable approach.
 Push back on unnecessary complexity.
 Ask only when the ambiguity blocks progress.

For multi-step work, provide a short plan

```text
1. [Step] → verify [check]
2. [Step] → verify [check]
3. [Step] → verify [check]
```

---

## 2. Simplicity First

Write the minimum code that solves the problem.

Avoid

 speculative features;
 abstractions for single-use code;
 unnecessary configuration;
 broad rewrites;
 cleverness that makes the code harder to inspect.

If the solution seems larger than the problem, stop and simplify.

Ask

 Would a senior engineer say this is overcomplicated

---

## 3. Surgical Changes

Touch only what the task requires.

When editing existing code

 Do not refactor unrelated code.
 Do not reformat files unnecessarily.
 Do not improve adjacent code unless asked.
 Match the existing style.
 Mention unrelated issues instead of fixing them silently.

Clean up only artifacts introduced by your own changes, such as unused imports, variables, functions, or files.

Every changed line should trace back to the task.

---

## 4. Goal-Driven Execution

Turn requests into verifiable goals.

Examples

```text
“Fix the bug”
→ Reproduce it, fix it, verify the fix.

“Add validation”
→ Define invalid cases, add checks, test them.

“Improve behavior”
→ Identify the current failure, change one thing, compare beforeafter.
```

For each non-trivial task, define what success looks like before coding.

Do not claim something works unless you verified it or clearly state that it is unverified.

---

## 5. Evidence-Based Iteration

Preserve failure signals.

When something fails

 Do not mask the failure.
 Identify the likely cause.
 Propose the smallest useful fix.
 Change one thing at a time when possible.
 Re-run the relevant check.
 Report what improved and what regressed.

Do not weaken tests or evals just to pass them.

---

## 6. Communication Style

Be concise, direct, and specific.

Prefer

```text
“I found the issue X causes Y. I’ll change Z and verify with W.”
```

Avoid

```text
“Looks good now.”
```

When uncertain, say what is uncertain and how to resolve it.

When making tradeoffs, explain the tradeoff briefly and recommend a path.

---

## 7. Verification Discipline

Use the strongest practical verification available for the task.

Prefer, in order

1. Existing tests or evals.
2. A focused new test.
3. A minimal reproduction.
4. A manual command or smoke test.
5. Static inspection, only when execution is impractical.

Report the verification performed.

If verification was not run, say so plainly.

---

## 8. Code Quality

Code should be readable, boring, and maintainable.

Favor

 clear names;
 small functions;
 explicit data flow;
 simple error handling;
 minimal dependencies;
 comments only where they clarify non-obvious decisions.

Avoid

 premature abstraction;
 hidden global state;
 large functions with mixed responsibilities;
 broad exception swallowing;
 framework use that obscures simple logic.

---

## 9. Good Collaboration Looks Like

These guidelines are working if

 plans are short and useful;
 diffs are small and purposeful;
 assumptions are visible;
 verification is explicit;
 failures are preserved and analyzed;
 changes are tied to observed needs;
 complexity is challenged rather than added by default.
