# Generic Research Workflow Changes for Delegate

**Date**: 2026-05-16
**Status**: Spec draft. Technology- and domain-neutral. Describes the
role structure, workflow stages, MCP tool, audit primitives, and
charter additions that turn delegate's current single-researcher
loop into an advisor / executor / committee process suitable for
any iterative experimentation domain.

> **TL;DR.** Adds three things to delegate's existing research
> workflow: (1) an explicit **advisor / executor / committee** role
> triangle (advisor = strong model, executor = cheap model — already
> the `researcher` / `researcher_assistant` pair today, but the
> charter doesn't say so; committee = new strong-model adversarial
> reviewer); (2) a **`committee_review_request` MCP tool** that
> spawns a fresh committee agent at any of three checkpoints (plan
> review, milestone review, final review); (3) a new **`auditing`
> workflow stage** between `researching` and `reporting` that runs a
> registered set of mechanical invariant checks against the
> experiment artifact. All terminology in this doc is domain-neutral.

---

## 1. Scope and motivation

Delegate's current research workflow is `todo → researching →
reporting → done` (with `paused` and `cancelled`). A single
researcher agent runs the experiment loop, optionally delegating
log-reading and background-job management to a
`researcher_assistant`. Two structural gaps:

1. **No adversarial review checkpoint.** The researcher self-audits
   before transitioning to `reporting`. In any domain where
   confirmation bias or hidden methodological leaks are common
   (machine learning, scientific computing, simulation, quantitative
   analysis), a separate adversarial reviewer catches a class of
   failures that self-review systematically misses.

2. **No mechanical audit stage.** Some failure modes are mechanical
   and can be caught by automated invariant checks (off-by-one in
   time-aligned signals, accidental future-information use,
   results that exceed an absolute oracle-class upper bound).
   Nothing in the current workflow runs such checks before promotion.

This spec adds:

- A **committee role** (adversarial reviewer)
- An **`auditing` workflow stage** between `researching` and
  `reporting` for mechanical invariant checks
- Three **committee review checkpoints**: `plan_review`,
  `milestone_review`, `final_review`
- A clarified **advisor / executor split** in the existing
  researcher charter

---

## 2. The role triangle

| Role | Model class | Primary job |
|---|---|---|
| `researcher` (advisor) | strong | hypothesis design, direction-setting, audit assistant's work, prepare materials for committee, integrate committee feedback |
| `researcher_assistant` (executor) | cheap | run experiments via `run_background`, digest logs, run cheap audits, draft knowledge-base updates, write scaffolding |
| `committee` (adversary) | strong, separate instance | adversarial review at three checkpoints, propose specific extra-data experiments that would resolve their concerns |

The advisor / executor pair already exists in delegate; this spec
clarifies the advisor charter to make the relationship explicit
(advisor is to executor as PhD advisor is to PhD student) and adds
the committee as a new role.

### 2.1 Why a separate committee

1. **Confirmation bias is a real failure mode.** A researcher
   reviewing their own plan looks for confirmation; a separate
   adversary asks "what would convince me you're wrong?".
2. **Mechanical audit and adversarial review are complementary.**
   Audit checks catch mechanical bugs (off-by-one, leakage, absolute
   implausibility). Adversarial review catches scientific blind
   spots (sub-population fitting, universe selection bias,
   optimism in cost / runtime / generalisation models).
3. **The committee's contribution is uniquely framed.** Its job is
   to convert a vague concern into a *specific extra experiment that
   would resolve the concern*. That framing reliably produces
   data-demands the researcher cannot self-generate.

---

## 3. Workflow stage additions

The current research workflow:

```
todo → researching → reporting → done
                ↕
              paused
```

The proposed addition inserts one new stage (`auditing`) and three
committee checkpoints (which are MCP-tool invocations, not separate
workflow stages):

```
todo
  → researching
       │ ★ committee:plan_review        (one-shot, after plan written)
       │ ☆ committee:milestone_review*  (optional, on researcher request)
       ↓
  → auditing                            (NEW STAGE)
       │  mechanical invariant checks
       ↓
       │ ★ committee:final_review        (one-shot, after audit pass)
       ↓
  → reporting
       ↓
  → done

  ★ = binding committee gate
  ☆ = advisory committee touchpoint
```

The state machine still has the same terminal structure (`done`,
`cancelled`). The new `auditing` stage is a workflow-engine stage;
the committee checkpoints are MCP-tool invocations within existing
stages.

### 3.1 `plan_review` checkpoint

- **Trigger**: researcher transitions task into `researching`, then
  immediately calls
  `committee_review_request(task_id, "plan_review", [...])`
- **Materials**: plan document, hypothesis spec, stress-test write-up
- **Output**: written review with verdict
  `APPROVE | REVISE_WITH_DATA | REJECT`
- **Binding**: yes; advisor can override with logged justification
- **Re-review**: allowed once if verdict was `REVISE_WITH_DATA`

### 3.2 `milestone_review` checkpoint

- **Trigger**: researcher calls
  `committee_review_request(task_id, "milestone_review", [...])` at a
  self-defined milestone during experiment execution
- **Materials**: results-so-far, current artifact directory
- **Output**: verdict `PROCEED | ADD_DATA | HALT`
- **Binding**: advisory

### 3.3 `auditing` stage (mechanical)

- **Trigger**: researcher transitions task from `researching` to
  `auditing` when an experiment completes
- **Implementation**: an invariant-check script
  (`delegate/audits/runner.py`) runs against the experiment artifact
  directory
- **Default checks** (domain-pluggable):
  1. **Time-shift invariant** — shift labels by ±1 sample; metric
     must not improve
  2. **Sampling-anchor invariant** — rerun with the alternate timing
     convention (e.g., interval-end vs interval-start); metric must
     not improve
  3. **Future-blind warmup invariant** — confirm the first N samples
     produce NaN / undefined signal, not zero
  4. **Plausibility ceiling** — candidate metric must be
     ≤ `K × oracle_benchmark` for the domain, where `K` and the
     oracle source are domain-pluggable (see §6)
  5. **Shuffled-label sanity** — rerun with shuffled labels; metric
     must collapse to baseline
  6. **Noise-injection robustness** — add increasing noise to inputs;
     metric must degrade monotonically
- **Output**: `audit_status: passed | failed_<reason>` written to
  artifact directory
- **Binding**: pass required to transition to `reporting`. Without
  a registered oracle ceiling, the plausibility check is skipped
  with a warning logged.

### 3.4 `final_review` checkpoint

- **Trigger**: researcher transitions task from `auditing` to
  `reporting` after audit pass, then immediately calls
  `committee_review_request(task_id, "final_review", [...])`
- **Materials**: experiment artifact + audit-pass certificate
- **Output**: written review with verdict
  `PROMOTE | DEFER_WITH_EXTRA_DATA | KILL` plus an
  `adversarial_questions` list (each question must be answerable
  ONLY with more data)
- **Binding**: yes; override requires explicit human-reviewer
  sign-off

---

## 4. New MCP tool: `committee_review_request`

```python
@tool(
    "committee_review_request",
    "Request an adversarial committee review at a defined checkpoint.",
    {
        "task_id": {
            "type": "integer",
            "description": "Task ID to review",
        },
        "checkpoint": {
            "type": "string",
            "enum": ["plan_review", "milestone_review", "final_review"],
            "description": "Which checkpoint to invoke",
        },
        "materials_paths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Files the committee should read",
        },
    },
)
async def committee_review_request(args):
    """
    1. Spawn a fresh `committee`-role agent on the same artifact dir
    2. Inject the committee charter + the materials
    3. Wait for the committee's review document
       (saved to {artifact_dir}/committee/{checkpoint}-{timestamp}.yaml)
    4. Return the review document inline to the requester
    """
```

The committee agent is short-lived: single review, then exits. It
does not retain context across reviews — each invocation is fresh.
This is intentional: the committee should bring a clean adversarial
read each time, not "remember" prior reviews of the same hypothesis.

Cost containment: see §7 for the per-hypothesis invocation budget.

---

## 5. Charter additions

### 5.1 Researcher (advisor) charter — additions

Add to `charter/roles/researcher.md`:

```markdown
### Advisor Role

You are the advisor in an advisor / executor / committee team. Your
scarcest resources are your context window and your judgment.
Delegate all execution to your assistant. Reserve your turns for:

1. Hypothesis design and direction-setting
2. Auditing your assistant's work (one-line summaries are fine; only
   open logs when something doesn't reconcile)
3. Preparing materials for committee reviews
4. Integrating committee feedback into the plan
5. Final decisions on verdicts

You are not the executor. If you find yourself reading raw training
logs or writing scaffolding code, redirect that work to your
assistant.

### Committee Interface

Three committee review checkpoints exist:

1. **plan_review** — after writing the hypothesis + stress-test, and
   before running any compute. Call
   `committee_review_request(task_id, "plan_review", [<plan path>])`.
   Wait for verdict. If `REVISE_WITH_DATA`, gather the demanded data,
   then call once more. If `REJECT`, log the kill and move on.

2. **milestone_review** — optional. At any self-defined milestone
   during the experiment phase, call
   `committee_review_request(task_id, "milestone_review", [<results-so-far>])`.
   Treat the verdict as advisory.

3. **final_review** — mandatory after the audit stage passes. Call
   `committee_review_request(task_id, "final_review", [<artifact + audit-pass>])`.
   `DEFER_WITH_EXTRA_DATA` is the modal verdict; gather the
   demanded data, then resubmit one more time.

The committee's purpose is to catch what you can't see. Treat their
demands seriously. Override only with written justification logged
in the task atlas.
```

### 5.2 Researcher Assistant charter — additions

Add to `charter/roles/researcher_assistant.md`:

```markdown
### Audit-Stage Support

When the researcher transitions a task to `auditing`, run the
audit-invariant script (`delegate/audits/runner.py`) and forward the
result. Do not try to interpret a failed audit — pass the failure
detail to the researcher verbatim. Save the audit-pass certificate
to the artifact directory.
```

### 5.3 Committee charter (NEW)

Create `charter/roles/committee.md`:

```markdown
## Committee Role

You are the adversarial reviewer. You are NOT a collaborator. Your
job is to find the way a hypothesis or result is wrong, and to
recommend the specific extra-data experiment that would resolve your
uncertainty.

### Single-Invocation Discipline

You exist for ONE review at a time. You do not retain context across
reviews. Each invocation is a fresh agent reading the materials
provided.

### What you do

1. Read all materials provided.
2. Identify the strongest attacks on the hypothesis or result.
3. For each attack, decide whether existing evidence answers it.
   - If yes, mark the attack `answered`.
   - If no, mark the attack `requires_more_data` and propose the
     specific extra experiment.
4. Issue a verdict per checkpoint type (see "Checkpoint verdicts").
5. Write your review as a structured YAML document to
   `{artifact_dir}/committee/{checkpoint}-{timestamp}.yaml`.
6. Exit.

### Discipline

- You do NOT propose your own hypotheses. You only review.
- You do NOT execute experiments. Your output is a review document.
- You do NOT decide the verdict alone — your verdict is binding only
  for `plan_review` and `final_review`, and even there the advisor
  can override with logged justification.
- You DO ask the most adversarial questions you can construct, and
  you DO insist on extra-data answers when self-evidence is
  insufficient.

### Checkpoint verdicts

- `plan_review`: `APPROVE` | `REVISE_WITH_DATA` | `REJECT`
- `milestone_review`: `PROCEED` | `ADD_DATA` | `HALT`
- `final_review`: `PROMOTE` | `DEFER_WITH_EXTRA_DATA` | `KILL`

### The "more data" framing

Your most valuable contribution is converting a vague concern
("something feels off") into a specific extra experiment.

Bad review:
> "I'm not sure this is robust."

Good review:
> "Robustness is unproven — to be convinced, I'd need
> [specific extra experiment X] with [expected outcome Y]; if you
> see [outcome Z instead], the hypothesis is dead."

Every `requires_more_data` mark must point at a specific, runnable
extra experiment. If you cannot name the experiment, the attack
isn't sharp enough to use — drop it or rewrite it.

### Review format

```yaml
checkpoint: plan_review | milestone_review | final_review
verdict: <one of the verdicts for this checkpoint type>
strongest_attacks:
  - attack: "<one-sentence attack>"
    status: answered | requires_more_data
    answer: "<existing-evidence citation>"     # if answered
    data_demand: "<specific extra experiment>" # if requires_more_data
adversarial_questions:                          # only for final_review
  - "<question that can only be resolved with more data>"
data_demands:                                   # consolidated, only if needed
  - "<extra experiment 1>"
  - "<extra experiment 2>"
notes: "<any other relevant observations>"
```

Be concise. The advisor reads this; the assistant doesn't.

### What you do NOT do

- You do not message anyone. Your output is the review document.
- You do not run code. You read materials and write the review.
- You do not propose alternative hypotheses. If the hypothesis is
  flawed, mark it as such — the advisor will reformulate.
- You do not give vague feedback. Every concern must be either
  `answered` or paired with a specific extra-data demand.
```

---

## 6. Audit primitives (generic)

Implementation lives at `delegate/audits/`:

```
delegate/audits/
├── __init__.py
├── runner.py            # orchestrator — runs all registered checks against an artifact
├── invariants.py        # time-shift, sampling-anchor, future-blind-warmup
├── plausibility.py      # plausibility-ceiling check
├── shuffle.py           # shuffled-label sanity
├── noise.py             # noise-injection robustness
├── registry.py          # domain-specific ceiling registration
└── README.md            # how to register a domain
```

### 6.1 Registering a domain-specific plausibility ceiling

```python
from delegate.audits import register_ceiling

register_ceiling(
    domain="my_domain",
    # Compute the oracle-class upper bound for a given artifact.
    # Returns a scalar in the same units as the candidate metric.
    ceiling=lambda artifact_dir: compute_oracle(artifact_dir),
    # Candidate must be <= threshold * ceiling. Default 0.5.
    threshold=0.5,
)
```

If no ceiling is registered for the artifact's declared domain, the
plausibility check is skipped with a warning logged into the audit
output (not a hard failure — but the audit certificate marks
`plausibility_ceiling: skipped`, and the committee should note this
at final review).

### 6.2 Audit artifact format

```yaml
audit_status: passed | failed
checks:
  time_shift: pass | fail | not_applicable
  sampling_anchor: pass | fail | not_applicable
  future_blind_warmup: pass | fail | not_applicable
  plausibility_ceiling: pass | fail | skipped
  shuffled_label: pass | fail | not_applicable
  noise_injection: pass | fail | not_applicable
ratios:
  plausibility_ratio: <candidate / ceiling>          # if check ran
notes:
  - "<observations, e.g. why a check was not_applicable>"
artifact_audit_id: T{NNNN}.audit
```

### 6.3 Researcher-supplied check exceptions

A check is `not_applicable` only if the researcher has declared it
inapplicable in `metadata.audit_exemptions`, with a written
justification. Exemptions appear in the audit certificate verbatim
and become part of the materials the committee reviews at
`final_review`.

---

## 7. Cost containment

| Resource | Bound |
|---|---|
| Plan reviews per hypothesis | 1, plus 1 re-review allowed if verdict was `REVISE_WITH_DATA` |
| Milestone reviews per running experiment | 1 by default; researcher may request more explicitly |
| Final reviews per audit-passed candidate | 1, plus 1 re-review allowed if verdict was `DEFER_WITH_EXTRA_DATA` |
| Audit-stage compute | bounded by the registered check set; framework defaults are O(experiment-time) per check, run sequentially or in parallel as configured |

Total committee invocations per promotable hypothesis: typically 2-3
(plan review + final review, occasionally one milestone). This is
small relative to the experiment compute budget for any non-trivial
domain.

---

## 8. Mapping to current delegate

| Component | Current state | Change |
|---|---|---|
| `workflows/research.py` | `todo → researching → reporting → done` + `paused`, `cancelled` | Add `auditing` stage between `researching` and `reporting`. Committee checkpoints are MCP-tool invocations, not new workflow stages. |
| `charter/roles/researcher.md` | Single researcher role, generic experiment loop | Add "Advisor Role" + "Committee Interface" sections |
| `charter/roles/researcher_assistant.md` | Sidekick role | Add "Audit-Stage Support" section |
| `charter/roles/committee.md` | does not exist | New file — adversarial reviewer charter |
| `mcp_tools.py` | 24 tools | Add 1: `committee_review_request` |
| `delegate/audits/` | does not exist | New module: 6 invariant primitives + ceiling registry |
| `runtime.py` / `prompt.py` | Hardware-context probes injected for `researcher` role | Same probes apply to `committee` role |
| Worktree / artifacts | exists for researcher | Committee reads the same artifact dir; writes its review to `{artifact_dir}/committee/` |

---

## 9. Acceptance criteria

- [ ] `committee_review_request` MCP tool exists and spawns a fresh
      committee-role agent
- [ ] Committee agent has read access to the artifact directory and
      to any materials passed in `materials_paths`
- [ ] Committee writes a YAML review document to
      `{artifact_dir}/committee/{checkpoint}-{timestamp}.yaml`
- [ ] `auditing` workflow stage exists and blocks transition to
      `reporting` until audit-runner exits with `audit_status: passed`
- [ ] Domain-specific plausibility ceilings can be registered via
      `delegate.audits.register_ceiling(...)`
- [ ] Researcher charter is explicit about advisor / executor /
      committee distinction
- [ ] Committee charter exists and frames the role as adversarial-only
- [ ] Override of a binding committee verdict requires logged
      justification (atlas entry with `override_reason`)

---

## 10. Out of scope (v1)

- Multi-member committees (panel of 2+ adversaries with disagreement
  resolution mechanics)
- Committee learning across reviews (each invocation is fresh — no
  retained context)
- Cross-team committee sharing
- Automated re-audit when underlying data refreshes
- Live-monitoring integration (covered by a separate downstream spec)
- Automatic promotion of recurring committee questions into mandatory
  audit checks (manual today: human reviewer reads
  `committee_log.md` and decides what to formalise)

---

## 11. References

| Source | Why |
|---|---|
| `~/code/delegate/delegate/workflows/research.py` | Current state machine |
| `~/code/delegate/delegate/charter/roles/researcher.md` | Current researcher charter — receives the advisor framing |
| `~/code/delegate/delegate/charter/roles/researcher_assistant.md` | Current assistant charter — minor addition |
| `~/code/delegate/delegate/mcp_tools.py` | Where `committee_review_request` is registered |
| `~/code/delegate/docs/architecture.md` | Adapter system, charter addons — the audits registry follows the same pattern |
| `~/code/delegate/docs/ml-research-gaps.md` | Earlier gap analysis that produced delegate's current ML support |
| `~/code/rana_trading/docs/process/hypothesis_lifecycle.md` | One project-specific lifecycle that motivated this spec (concrete examples + audit-gate origin story) |
