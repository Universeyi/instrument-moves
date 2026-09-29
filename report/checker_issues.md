# Checker issues

Runs where the recorded verdict and what the agent actually did appear to
disagree. These are **not** variance: a task that flips because the environment
was slow is noise, a task that flips because the verifier changed its mind about
identical behaviour is a bug. The two need to be kept apart, and this file is
where the second kind goes.

**Status: first pass done, 2026-08-27, over Arm A (29 tasks x 10 trials).**
One task defect confirmed, one question left open, four suspicions closed as
genuine agent failures. **No false positive or false negative was found.** The
summary at the end says what that does and does not support.

---

## How an entry gets here

1. `scripts/find_candidates.py` ranks runs worth opening. That ranking is a
   prior about where to look and never appears in this file as evidence.
2. A person opens `traj.json`, the screenshots, and `result.txt` for that run
   and decides what the agent did.
3. If — and only if — the recorded verdict disagrees with that, it is written
   up below, with enough detail that a reader can check it against the same
   artifacts without trusting the write-up.

Undecidable cases are recorded as undecidable. A case that looks wrong but
cannot be settled from the artifacts is more useful written down honestly than
dropped, and far more useful than one written down confidently.

### Classification

| | |
|---|---|
| **false positive** | scored as passed; the agent did not complete the task |
| **false negative** | scored as failed; the agent did complete it |
| **task defect** | the task cannot be completed as specified, or its success condition is ambiguous |
| **undecidable** | the artifacts do not settle it |

Upstream has already fixed bugs in all of the first three categories
(`8ae5064`, `c33e340`, `32ed916`, `702e904`), so an entry here is a report of a
recurrence class, not an accusation.

---

## Entry template

Copy this block per issue. Keep every path repository-relative.

```markdown
### <n>. <TaskName> — <one-line summary>

| | |
|---|---|
| task_id | `TaskName` |
| run_id | `<hex from data/runs.jsonl>` |
| arm / trial | `A_temp0` / trial N |
| recorded | `score: X` — `reason: "..."` verbatim from result.txt |
| my verdict | pass / fail |
| classification | false positive / false negative / task defect / undecidable |
| verifier at | `<task-set commit these runs used>` |

**Evidence**

- `runs/<arm>/<TaskName>/trial_NNN/<TaskName>/screenshots/<file>.png` — what it shows
- `traj.json` step N: the action taken, quoted

**Reasoning**

Why the artifacts show something other than what was recorded. State what the
verifier checks (with a `file:line` citation into `vendor/MobileWorld`) and
which part of that check the run satisfies or fails.

**Counter-argument**

The strongest case that the recorded verdict is right and this reading is
wrong. If there isn't one, say so.

**Reproducing**

The smallest thing that would settle it — usually re-running this one task with
`--task <TaskName>` and comparing.
```

---

## Entries

### 1. ReadQwen3PaperTask5 — exact string match on a free-text answer, in a notation the source document does not use

| | |
|---|---|
| task_id | `ReadQwen3PaperTask5` |
| arm / trials | `A_temp0`, all 10 trials |
| recorded | `score: 0.0` — `reason: "incorrect. Expected: vie Latn,khm Khmr, Got: "` (10/10) |
| classification | **task defect** |
| verifiable from | the public task definition alone; no run artifacts needed |

**Evidence** — `src/mobile_world/tasks/definitions/native/read_paper_5.py`:

```python
CORRECT_ANSWER = "vie Latn,khm Khmr"
...
answer_normalized = ",".join(part.strip() for part in answer_normalized.split(","))
if answer_normalized == self.CORRECT_ANSWER:
    return 1.0, "success"
```

The goal asks the agent to read a PDF and "tell me what kind of Austroasiatic
language is supported by Qwen3 in Belebele Benchmark", as "a list of languages
separated by comma".

**Reasoning.** Scoring is an exact string comparison. The only normalisation is
stripping whitespace around commas. Therefore:

- **"Vietnamese, Khmer"** — the correct answer in ordinary English — scores 0.
- **"khm Khmr,vie Latn"** — the correct two languages in the other order —
  scores 0. Nothing in the goal specifies an order.
- **"vie_Latn,khm_Khmr"** scores 0. This matters, because the underscore form
  is the standard FLORES-200 / Belebele notation. An agent that reads the paper
  and reproduces the notation printed there is penalised for doing so; the
  expected string uses a space where the source uses an underscore.

A task whose success condition is an undocumented exact spelling is measuring
whether the agent guessed a format, not whether it read the paper.

**Counter-argument.** The goal does say "No other text", so some formatting
strictness is intended. That justifies rejecting prose, not rejecting a
different separator between the language and script subtags, and it does not
justify being order-sensitive when the goal never mentions order.

**Status of the observed failures.** In all 10 of our trials the answer reached
the verifier **empty** (`Got:` with nothing after it), so *these particular*
runs did not fail because of the formatting rule — they failed with no answer
recorded at all. Whether the agent never emitted one or the answer never
reached `controller.interaction_cache` is **not resolved**: it needs the
trajectories, which are on the evaluation host. Two separate issues, and only
the first is settled here.

**Reproducing.** Read the file above; no execution required. For the empty-answer
question: `--task ReadQwen3PaperTask5` and inspect `traj.json` for an `answer`
action.

---

### Closed after inspection — genuine agent failures, not scoring bugs

Recorded so a later pass does not re-open them.

| task | why it was suspected | verdict |
|---|---|---|
| `MattermostEmailTask` | verifier rejected a subject that visibly *did* contain a tracking code (`REF-XYZ-123`) | **verifier correct.** The task's real code is `TT-POC-2025-BLPINE-042`; the agent invented a plausible-looking one instead of reading it from Mattermost |
| `MastodonGetServerInfoTask` | 10/10 failures reporting `actual: Hello from Owner 👋` — not a database size | **verifier correct.** It reads the owner's *latest* toot (`get_latest_toots_by_username(..., limit=1)`); the greeting is the seeded toot, so the agent never posted as the owner. The task requires switching accounts first |
| `MastodonShareLocationTask` | 10/10 failures reporting a question, not a URL, as the toot text | **verifier correct**, same shape: the seeded toot is still the latest, so the required post was never made |
| `CheckEventTimeTask` | flipped 2/3 in the all-pass bucket | **verifier correct.** The traces agree for six steps, diverge at step 7, and the failing run never sets an alarm. Detail in the project handoff §6b |

---

### Noted, not filed as issues

- **`MastodonShareLocationTask` error text names the wrong thing.** The message
  reads `does not contain {self.EXPECTED_URL}` while the code actually tests the
  regex `https://maps\.app\.goo\.gl/\w+`. Harmless to scoring, misleading to
  anyone debugging from the message.
- **Success reasons are inconsistent across tasks** — `Success`, `success`,
  `correct answer`, `Correct email sent`, and `No reason provided for <Task>`
  all mean "passed". Only cosmetic, but it makes log-based analysis need
  special cases.
- **Eleven of the 29 pilot tasks were never passed in ten trials.** Ten of
  those eleven were also failed by every published model. Nine returned a
  byte-identical failure reason on all ten trials:
  `CheckInterviewTimesTask`, `GraduationMassEmailTask`,
  `LocalFileManagementTask2`, `MastodonGetServerInfoTask`,
  `MastodonServerInfoReportTask`, `MastodonShareLocationTask`,
  `MattermostResourceConflictResolutionTask`, `ReadQwen3PaperTask5`,
  `SMSManagement`.

  A task nothing has ever passed contributes a constant to every score: it
  cannot separate one model from another, and it cannot flip, so it lowers
  every entry equally and adds nothing to the comparison the leaderboard
  exists to make. This is a benchmark-composition observation, not a bug — but
  it belongs in any discussion of what the headline number measures.

  One exception is worth its own look: **`GraduationMassEmailTask` sits in the
  *split* bucket** — published runs disagreed on it, and the published
  Kimi-K2.5 entry *passed* it — yet all ten of our trials failed with the same
  `No email found`. Whatever the cause, it is not "no model can do this". Not
  investigated; it is the first thing to open next.

---

## Summary

Filled in once there are entries. Reported as counts by classification, against
the number of runs inspected — a rate is only meaningful with its denominator,
and the denominator is "runs opened by hand", not "runs executed", because the
inspection is deliberately targeted rather than random.

| classification | count |
|---|---|
| false positive | 0 |
| false negative | 0 |
| task defect | 1 |
| undecidable | 1 |
| closed as genuine agent failure | 4 |
| **tasks inspected** | **6** |

Six of the 29 tasks were examined, chosen by `scripts/find_candidates.py`
(minority outcomes inside unstable tasks first) plus the tasks whose failure
reason looked structurally wrong. That is a targeted sample, not a random one:
these counts describe what was found where the search was pointed, and support
no estimate of a rate across the suite.

**No false positives or false negatives were found.** In every case where the
verifier's message looked wrong, reading the task definition showed the
verifier was right and the agent had genuinely failed — twice in ways the error
message alone made look like a scoring bug (a hallucinated tracking code that
*looked* like a valid one; a seeded toot that *looked* like the verifier had
read the wrong field). The one confirmed defect is a scoring *design* problem
rather than a scoring error, and it is provable from the public source with no
run data at all.

This is a weaker result than the project expected going in, and it is reported
as it stands.
