# TaskLean benchmark protocol

The product claim to test is **lower total cost per successfully completed task at unchanged quality**, not simply a shorter initial prompt.

## First experiment

Use 12 small tasks drawn from real work: four bug fixes, four test additions, and four explanation/review tasks. Include short prompts, repetitive long prompts, and long prompts with dense constraints. Define each task's expected behavior, allowed changes, relevant tests, and review rubric before running it.

Run at least three independent repetitions per task and arm. Begin with one execution model and one reasoning setting. Randomize baseline/optimized order. Use isolated identical starting checkouts and keep external state, ignored files, global instructions, enabled plugins, tools, and configuration fixed. Record versions. Reusing model responses or warming caches for only one arm invalidates the comparison.

Compare these arms:

1. Original prompt; no optimizer inference.
2. Local cleanup; no optimizer inference.
3. Model rewrite; include preparation inference even when the rewrite is rejected.

Do not mix automatic model routing into the first test. Once prompt rewriting shows a benefit, test model selection as a separate intervention so you can attribute the result.

## Record every attempt

Save preparation plans, event streams, run records, test outputs, and human review evidence. Review without knowing which arm generated the result when feasible. Grade whether every original requirement was fulfilled and whether unwanted changes or semantic drift appeared.

Report success rate, input/cache/output tokens, wall time including preparation, and failed/retried attempts. Calculate dollar cost separately using the rates applicable at execution time, distinguishing cached tokens and models. Codex account usage is not inferred from token counts.

An API timeout can have unknown cost. Mark it unknown and resolve it against provider records if possible; do not treat it as free. Include all retries in an arm's aggregate, including failures that never produced a successful result. Report the fraction with missing cost data.

## Analysis

For each arm, compute total recorded cost across all attempts divided by the number of successfully completed tasks, alongside the success rate. Also report total tokens per success, completion time distribution, and requirement-loss rate. A zero-success arm has undefined/infinite cost per success, not zero cost.

Use paired task-level results and uncertainty intervals. Show task categories separately, and avoid selecting only cases where optimization won. Character-based prompt estimates are only diagnostic; measured task usage decides the result.

The current CLI validates and compares a pair. A benchmark-wide aggregator, automatic tests/grades, and statistical analysis are future work. Until the full experiment is complete, label every result as an observation rather than a general savings claim.

## Shipping decision

Set an acceptable quality non-inferiority margin and a minimum economically meaningful savings target before inspecting outcomes. Ship automatic rewriting only for task categories that meet both. Leave short or constraint-heavy prompts unchanged when there is no established benefit. Roll back when monitored quality degrades.

Potential commercial value is repeatable measurement plus context selection and routing. A rewriter alone is easy to copy and can cost more than it saves; the experiment should establish where the product actually helps.
