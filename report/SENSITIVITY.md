# Sensitivity: does the result survive removing round 6?

Round 6 is the obvious thing for a reader to attack, and it deserves the
attack. Within the headline regime (rounds 4–10) it accounted for:

- **68 of the 95 silent in-task retries** (72%),
- **all 6 runs that produced no `result.txt`**,
- and it recorded **the highest suite score of the regime**, 39.13%, on a
  reduced denominator of 23 scored tasks instead of 29.

The provider returned HTTP 401 for roughly an hour during it.

Everything below is generated from `data/runs.jsonl` by
`scripts/analyze.py --trials 4:10` with and without `--exclude-trials 6`, and
is recorded in `data/sensitivity_round6.json`.

---

## Headline figures, with and without round 6

| | with round 6 | without round 6 | change |
|---|---|---|---|
| complete rounds | 7 | 6 | −1 |
| suite scores (%) | 31.0, 31.0, 39.1, 34.5, 37.9, 37.9, 34.5 | 31.0, 31.0, 34.5, 37.9, 37.9, 34.5 | — |
| **best − worst spread** | **8.10 pp** | **6.90 pp** | **−1.20** |
| re-run 95% interval (29-task scale) | 27.6–48.3% | 24.1–48.3% | — |
| **σ of a single run (29-task scale)** | **5.87 pp** | **5.92 pp** | **+0.06** |
| extrapolated to 117 tasks, 95% width | 12.82 pp | 11.11 pp | −1.71 |
| **minimum detectable difference (95%)** | **8.67 pp** | **8.15 pp** | **−0.52** |
| tasks that flipped | 8 / 29 | 7 / 29 | −1 |
| published adjacent gaps inside the noise | 16 / 17 | 16 / 17 | **0** |
| silent retries | 95 | 27 | −68 |

The single task that stops flipping is **`DownloadSendReceiptTask`**. No task
flips only when round 6 is removed.

## Reading it honestly

**The best−worst spread does weaken, by 1.2 points, and this must be stated.**

Two things support reading that as a property of the statistic rather than as
evidence about round 6, and the second is a computation, not an argument:

- A best−worst range is an order statistic. It cannot grow when a point is
  removed, and it grows on average with the number of repetitions, so a 7-round
  range and a 6-round range are not comparable quantities.
- **Leave-one-round-out over all seven rounds** (`analyze.py --leave-one-out`,
  recorded in `data/analysis.json`) shows the range is unmoved by dropping any
  round except the one holding the maximum:

  | round dropped | 4 | 5 | **6** | 7 | 8 | 9 | 10 |
  |---|---|---|---|---|---|---|---|
  | best−worst (pp) | 8.10 | 8.10 | **6.90** | 8.10 | 8.10 | 8.10 | 8.10 |
  | MDD (pp) | 9.00 | 8.52 | **8.15** | 8.42 | 8.02 | 8.76 | 9.01 |

  Only round 6 moves the range, and it does so because it holds the maximum.
  The MDD, by contrast, varies across every variant (8.02–9.01) and round 6 is
  not an outlier within that spread — it sits inside it.

**Every statistic that uses all the data instead of just the extremes barely
moves — but they do not all move the same way, and an earlier draft of this
file quoted only the one that suited the argument.** Both are given here:

| σ of a single run | 7 rounds | 6 rounds | change |
|---|---|---|---|
| 29-task (pilot) scale | 5.87 pp | 5.92 pp | **+0.06** |
| 117-task scale — the one the MDD is derived from | 3.13 pp | 2.94 pp | **−0.19** |

They move in opposite directions because they are computed differently: the
pilot-scale figure draws each task's rate from a Beta posterior, which widens
when a task loses a trial (n = 7 → 6), while the 117-task figure carries the
point estimates into strata, and four tasks' point estimates fell. Neither
change is large. The minimum detectable difference, which comes from the
117-task σ, moves 0.52 pp downward. The comparison against published entries is
unchanged at 16 of 17.

⇒ **The spread should not be the headline number.** σ and the minimum
detectable difference are the defensible ones, and they are stable under this
removal. This is a change to how the result is reported, made because of the
sensitivity check.

## Was round 6's high score an artefact of its short denominator?

This was the specific worry: 9 of 23 flatters a round whose six missing tasks
might have been the hard ones. **It is the other way round.**

| lost task | pass rate in the other six rounds |
|---|---|
| `AcceptMeetingTask` | 6/6 = 100% |
| `ScheduleLunchViaSmsTask` | 4/6 = 67% |
| `CVEmailTask` | 3/6 = 50% |
| `MattermostEmailTask` | 2/6 = 33% |
| `SendFormsTask` | 1/6 = 17% |
| `MattermostCreateChannelTask` | 0/6 = 0% |
| **mean of the lost tasks** | **44.4%** |
| mean of all tasks, other rounds | 34.5% |

The tasks the outage cost were **easier than average**. Imputing them at their
own observed rates puts round 6 at **(9 + 2.67) / 29 = 40.2%**, slightly
*above* the 39.13% actually recorded.

⇒ The outage **depressed** round 6 rather than inflating it. The observed
spread is not manufactured by the reduced denominator, and excluding round 6
removes a legitimate high point rather than a corrupted one.

## Per-task pass rates, both versions

Only **4 of 29 tasks** change at all. Round 6 contributed a scored result to
23 tasks, and removing it moves the rate of four of them.

| task | bucket | 7 rounds | 6 rounds | Δ pp |
|---|---|---|---|---|
| `AcceptMeetingTask` | all_pass | 6/6 | 6/6 | +0.0 |
| `BidFileRenameTask` | split | 0/7 | 0/6 | +0.0 |
| `CVEmailTask` | split | 3/6 | 3/6 | +0.0 |
| `ChangeWallpaperTask` | all_pass | 7/7 | 6/6 | +0.0 |
| `CheckEventTimeTask` | all_pass | 0/7 | 0/6 | +0.0 |
| `CheckInterviewTimesTask` | all_fail | 0/7 | 0/6 | +0.0 |
| `CloseFlightModeTask` | all_pass | 7/7 | 6/6 | +0.0 |
| `DownloadSendReceiptTask` | split | 1/7 | 0/6 | -14.3 |
| `GoogleMapsAlibabaSouthNeighborTask` | all_pass | 7/7 | 6/6 | +0.0 |
| `GraduationMassEmailTask` | split | 0/7 | 0/6 | +0.0 |
| `LocalFileManagementTask2` | all_fail | 0/7 | 0/6 | +0.0 |
| `MastodonCalendarMultiMemosTask` | all_fail | 0/7 | 0/6 | +0.0 |
| `MastodonChangeHeaderTask` | all_pass | 7/7 | 6/6 | +0.0 |
| `MastodonGetServerInfoTask` | all_fail | 0/7 | 0/6 | +0.0 |
| `MastodonImportMutedUsersTask` | split | 2/7 | 1/6 | -11.9 |
| `MastodonNewPostTask` | all_pass | 7/7 | 6/6 | +0.0 |
| `MastodonServerInfoReportTask` | all_fail | 0/7 | 0/6 | +0.0 |
| `MastodonShareLocationTask` | all_fail | 0/7 | 0/6 | +0.0 |
| `MattermostBudgetApprovalPipelineTask` | split | 2/7 | 2/6 | +4.8 |
| `MattermostCreateChannelTask` | split | 0/6 | 0/6 | +0.0 |
| `MattermostEmailTask` | split | 2/6 | 2/6 | +0.0 |
| `MattermostProjectStatusReportTask` | all_fail | 0/7 | 0/6 | +0.0 |
| `MattermostResourceConflictResolutionTask` | all_fail | 0/7 | 0/6 | +0.0 |
| `OpenFlightModeTask` | all_pass | 7/7 | 6/6 | +0.0 |
| `ReadQwen3PaperTask5` | all_fail | 0/7 | 0/6 | +0.0 |
| `SMSManagement` | all_fail | 0/7 | 0/6 | +0.0 |
| `ScheduleLunchViaSmsTask` | split | 4/6 | 4/6 | +0.0 |
| `SendFormsTask` | split | 1/6 | 1/6 | +0.0 |
| `TakeSelfieTask` | all_pass | 6/7 | 5/6 | -2.4 |

The four that move: `DownloadSendReceiptTask` (-14.3 pp), `MastodonImportMutedUsersTask` (-11.9 pp), `MattermostBudgetApprovalPipelineTask` (+4.8 pp), `TakeSelfieTask` (-2.4 pp).

## What goes in the paper

1. Report σ and the minimum detectable difference as the headline, not the
   best−worst spread.
2. State both spreads, 8.1 pp over seven rounds and 6.9 pp over six, and say
   why the second is smaller.
3. State that round 6 caught a provider outage, that it holds the maximum, and
   that imputation shows the outage cost it height rather than lending it any.
4. Keep round 6 in the headline. Removing a round because it contains an
   incident, when the incident demonstrably did not help it, would be selecting
   on the outcome.
