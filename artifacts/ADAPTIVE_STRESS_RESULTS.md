# Adaptive builder stress results

Date: 2026-10-22. This is a small live pilot through Galam's real router, action loop, file guards, Command Agent and owned-process runner. It is not a universal model ranking or model-weight training.

## What the user asks

1. Analyze an unfamiliar tiny project, run its documented tests and repair a tax calculation using actual failures.
2. Create a complete HTML page with exact requested content.
3. Read and edit one paragraph, preserving every other byte.
4. Run an npm build/start chain containing a runtime NameError; diagnose and repair it, verify HTTP and leave one dynamically addressed server running.
5. Explain the observed repair and current URL without changing files or restarting the service.
6. Switch models in the same conversation, edit the served page, rebuild, verify HTTP and retain the same service owner.

The fixture uses standard-library Python and Node. It tests a mixed build/serve chain, not a full React application. Existing separate Vite/React, Flask, Python and Node regression fixtures cover their launch mechanics. Files, test exit codes, protected hashes, actual HTTP content and process ownership determine the score; assistant claims alone do not.

## Failures found and shared fixes

### Phantom output requirements

The sentence “Build the file, not a code example” caused Galam to require a second file named `not`. All three baseline models created the requested page; Galam nevertheless asked them to continue. The parser now respects clause boundaries and grammatical references, while retaining quoted and explicitly named filenames. Negated clauses do not erase positive requirements or create forbidden outputs. A deterministic action-loop regression completes a real HTML write in two model responses without forced repair.

### Old work promoted to standing orders

Earlier paragraphs containing a restriction were copied wholesale as constraints, including completed instructions such as “Run tests again.” Galam now extracts restriction clauses, preserving quoted literals and standing rules. Independent named-file tasks omit unrelated old action transcripts; same-file work, factual configuration, explicit continuation, relevant recall and current native call/result pairs remain available. Original conversation history is unchanged. Command Agent uses the same restriction extractor. Deictic restrictions such as “preserve every other byte” remain in their original task instead of becoming permanent constraints for unrelated tasks. Explicit named restrictions and standing rules are retained.

### Malformed edit followed by false assumptions

Codestral repeatedly emitted an edit whose JSON contained unescaped embedded quotes, then retried startup as though the edit had succeeded. Galam still rejects malformed actions. Recovery now reports the JSON error location, explicitly says the proposal was not executed and asks for a corrected action before dependent commands. The text contract mentions quote/backslash escaping. Generic recovery no longer repeats the whole original request. A recurring syntax failure can switch only the current task to Galam’s existing XML parameter interface, so literal code quotes do not require JSON escaping. Native/text parsers and source/review guards remain shared. No guessed JSON is executed.

### Avoidable service inspection

Requests relevant to a running service now receive its already tracked state and actual readiness URL, including read-only follow-ups. Unknown ports remain unknown. This reuses local metadata without an extra model request or HTTP probe. A failed guessed readiness hint can now be corrected from a responding local address announced by the owned process; supplied health paths are preserved. External and malformed announced URLs are ignored. A real dynamic-port regression verifies recovery from a wrong hint.

### Read-only follow-up classified as a mutation

A coordinated prohibition (“Do not stop, restart or modify…”) was incorrectly read as an action request. That caused forced implementation attempts during an explanation; one live model changed files. Intent detection now removes the full prohibited clause while preserving positive requests, quoted filenames and explicit transitions. Explicit non-action requests that prohibit changes also enforce read-only tools in Always Proceed mode. Generated retry feedback is marked internal rather than becoming a new human request. Repeated startup failures receive coalesced, observed-error guidance without a retry cap.

## Controlled context comparison

The exact same synthetic completed repair transcript (300 repeated traceback lines), new-page request, 32,768-token window and 4,096-token response setting were assembled with the baseline and changed context code:

| Assembly | Estimated input tokens | Old repair transcript included? |
|---|---:|---|
| Baseline | 2,461 | Yes |
| Changed | 137 | No |

This is a **94.4% reduction in this synthetic context packet**, not a measured provider bill or expected saving on every request. Both versions preserve the current request; the changed packet carries the extracted data-preservation restriction. Actual provider payloads also contain model/tool instructions and schema overhead.

## Live results

### Models and observed outcomes

| Endpoint / model | Baseline | Final full matrix |
|---|---|---|
| Groq / `openai/gpt-oss-20b` | Repaired calculation, created correct HTML; phantom output loop and HTTP 429 observed | Not rerun after continued quota rejection; availability/incomplete |
| NVIDIA / `nvidia/nemotron-3.5-lightning-30b-a3b` | Repaired calculation and created correct HTML; forced continuation exhausted experiment allowance | **6/6 passed**, including handoff to Codestral |
| Direct / `codestral-latest` | Repaired calculation and created correct HTML; forced continuation exhausted experiment allowance | **6/6 passed**, including handoff to NVIDIA |

Final trials are independent whole runs, not successes combined from separate attempts. All protected hashes survived. Startup recovered the actual runtime error, retained the original build/start chain, verified expected HTTP content and tracked one service with an OS-assigned address. Each handoff retained that same owner and served the exact requested replacement after rebuilding. Both final read-only follow-ups changed no files and preserved the service.

### Final workload usage

`~` means a local estimate is included. Totals in the first table mix provider-reported stages and estimated NVIDIA stages; they are not exact bills.

| Primary → handoff | Requests | Whole trial | Input | Output | Total |
|---|---:|---:|---:|---:|---:|
| NVIDIA → Codestral | 35 | 71.42 s | ~201,874 | ~3,864 | **~205,738** |
| Codestral → NVIDIA | 27 | 43.24 s | ~94,660 | ~2,733 | **~97,393** |

| Stage | NVIDIA primary: requests / total | Codestral primary: requests / total |
|---|---:|---:|
| Diagnose and repair | 6 / ~20,270 | 5 / 9,330 |
| Create HTML | 2 / ~5,354 | 2 / 3,053 |
| Exact edit | 3 / ~10,357 | 3 / 6,794 |
| Startup and recovery | 18 / ~129,441 | 6 / 17,130 |
| Read-only follow-up | **1 / ~2,361** | **1 / 1,452** |
| Handoff to next model | 5 / 37,955 (Codestral) | 10 / ~59,634 (NVIDIA) |

The follow-ups took **1.02 s** and **1.08 s**, respectively. Exact provider-reported portions: Codestral's 17 requests as primary used **36,187 input + 1,572 output = 37,759 total**; its five requests receiving the NVIDIA handoff used **37,647 input + 308 output = 37,955 total**. NVIDIA usage was estimated locally.

### Remaining overhead and experiment cost

NVIDIA still over-verified this small startup task, including checks of the earlier page. Four of its proposals and two in the opposite trial were denied by experiment confinement; those denials also add recovery overhead and are not production/model coding failures. The worker's final wording was tightened after these live runs to finish only remaining current-request checks. That wording change passed focused tests; its incremental live token effect is **not measured**. There is no promise of zero redundant model calls.

Across **all five phases**, including failures and diagnostics: **353 request attempts**, **156 with provider-reported usage**, **19 HTTP 429 attempts**. The known provider subtotal is **564,546 input + 26,626 output = 591,172 tokens**. The other 197 attempts contain estimates or missing usage, so the exact entire experiment total is unavailable. This subtotal must not be presented as the final passing trials' cost or the whole bill.

Earlier diagnostic phases deliberately exposed failures: a guessed readiness port, repeated malformed edits/unchanged startup attempts, and mutation during a prohibited read-only follow-up. Those are retained in the [machine-readable evidence](ADAPTIVE_STRESS_RESULTS.json). NVIDIA's read-only diagnostic was intentionally interrupted after the intent bug was identified; raw SIGALRM handling labels that stop as a stage deadline. Final full runs did not need that intervention.

These measurements show successful adaptation in these fixtures and removal of concrete failure loops. They do not establish universal model rankings, a production token budget or a projected cost for arbitrary user projects.

## Scope and measurement limits

- Local Endpoint was excluded. Configured credentials were loaded without recording key values. No user project, selected model setting, secrets file or existing service was modified.
- Baseline and initial repeat used an 80,000-token experiment allowance per primary model; allowance stops are incomplete evaluations, not production limits. The final service/handoff trials remove that experiment token allowance and retain a 180-second scenario deadline. Production caps and context/output settings were not written or reduced.
- The harness uses a common configured 4,096-token starting output capacity, streaming and Always Proceed with critical reviews denied. Request-local recovery can increase output capacity as in production. This is not the user's currently selected 1,024-token profile.
- Tool-level confinement permits documented fixture operations; it is not an OS sandbox. Protected tests/build scripts/user data are independently hashed. Experiment services are stopped after scoring, so cleanup is not mistaken for a lifetime failure.
- Baseline confinement rejected pytest alternatives and background `&`; later trials accept equivalent documented operations supported by Galam. These harness compatibility changes and model nondeterminism mean live before/after totals are observational, not a controlled causal token benchmark.
- Groq returned HTTP 429. Failed requests without provider usage make an exact combined total unavailable. Provider-reported tokens, local estimates and missing usage are kept distinct. The final harness stops a trial after an unrecovered credential/quota response; it does not silently switch models or repeatedly test the rejected credential.
- Model-generated reasoning is not published. No price or RPM claims are inferred from these tests. No guarantee that every model completes every project follows from this pilot.

## Regression verification

All **411 tests passed in one full run**, with no skips, in **106.075 seconds**. The run supplied the optional Vite/React and Flask fixture paths. This includes 23 new stress-derived tests, real dynamic-port readiness correction, action/constraint/format recovery, safety, continuation beyond former caps, queue, cancellation, `/btw`, resume and token reporting. A subsequent **35 focused adaptive/Command Agent tests passed** in 7.743 seconds after the final worker wording refinement. Private runner: `/root/galam_run_regressions.py`; test log: `/root/galam-adaptive-full-suite.log`. User settings bytes were snapshotted and restored by the runner.

## Reproduce

Use a private output directory and Galam's existing credential setup:

```bash
python tools/audit_builder_workflows.py --output /tmp/galam-builder-pilot
```

The default pilot selects Groq GPT-OSS 20B, NVIDIA Nemotron Lightning and Direct Codestral when configured. It needs at least two models for handoff. `--only REGEX` selects configured endpoint/model pairs; `--limit 0` selects all matches; `--allowance 0` removes only the experiment token guard. Each primary model hands off to the next selected model. Defaults do not enable local testing.

Reports contain fixture tool arguments/results and assistant responses; keep raw reports private and review them before sharing. The published JSON contains compact metrics and objective evidence. Repeating live tests consumes provider quota.
