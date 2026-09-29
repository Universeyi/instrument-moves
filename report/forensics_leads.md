# Forensic leads from the published trajectory bundles

Zero compute. Everything here is computed from the 18 public bundles at the
pinned commit `83e7b8f` by `scripts/forensic_signals.py`, into
`data/forensic_signals.json`. A signal that fires is a place to open
trajectories by hand, not a finding. A signal that does not fire is written
down too, so that "not found" is not confused with "not checked".

Started 2026-09-03 as the zero-cost strand of the post-submission plan.
Nothing here touches `data/public_matrix.json`. It found two errors in the
version submitted for review; both are corrected in the arXiv version (see the
end of this note).

## Signals

| | signal | fires? | reading |
|---|---|---|---|
| S1 | runs at exactly the 50-step cap | (in paper) | 525 of 2,562 runs; per-entry cap rate ranges 9.3%–42.4%, so the budget does different amounts of work for different entries |
| S2 | passes in ≤3 steps | no | 19 such passes, all on three answer-type tasks (return a number / read a value) where a 2–3 step path is plausible |
| S3 | bundles with no parsable verdict | **against our pipeline** | 3 bundles carry verdicts in two formats the strict parser does not read; read leniently they reproduce their board numbers exactly |
| S4 | passes with no verifier reason | yes | 384 of 1,144 published passes; on 54 tasks no pass ever carries a reason |
| S5 | timestamp clustering | not computable | upstream records no timestamps; bundles are trajectory + result only |
| S6 | bundle reproduces its own board number | one entry | Kimi-K2.5: bundle 45.3% vs board 49.6% GUI-only. Disclosed in the entry's `notes`: the bundle is a 1-image-history run, the score a 3-image run whose logs were lost |

## What S3 turned out to be

Three bundles (MemGUI-Agent-235B, Qwen-UI-Agent, ForgeQwen3-8B) looked like
trajectories without verdicts. They are not. Their `result` strings are in two
formats the strict mirror of upstream's parser does not read:

```
25
score: 1.0
reason: Correct email sent
0
x-amz-checksum-crc64nvme:+BTZ+Z1CoCc=
```

(a chunked-transfer wrapper around the normal three lines), and

```
1.0
Correct email sent
```

(a bare score with no `score:` prefix). `scripts/build_public_matrix.py
--lenient` reads both and writes `data/public_matrix.lenient.json`. Read that
way, each of the three reproduces its leaderboard GUI-only figure to the decimal
(29.1, 82.1, 10.3). The strict build is unchanged and still reproduces the
as-submitted matrix byte for byte.

## What S6 says about the other 17

Fifteen entries reproduce their board number exactly or to within the rounding
of a few unscored tasks counted as fails on the board. One does not, and the
board says why in a `notes` field: the uploaded Kimi-K2.5 trajectories are from
a different configuration than the scored run. That is the reference entry the
audit's own configuration was chosen against. The check is cheap and nobody
seems to have run it; it belongs in any future forensic pass as the first step.

## S4 and adjudicability

384 published passes carry no verifier reason. For those runs a reader has the
agent's actions and a bare `1.0`. Whether that is enough to re-adjudicate a
disputed verdict depends on whether the observations were kept, which is what
`artefacts.observations` in the evidence-manifest spec now records, and why the
spec's checker flags a verdict with neither observation nor reason as
unadjudicable rather than wrong.

## Corrections (applied in the arXiv version)

Section 6, the round-cap sentence. As submitted: "497 of the 525 (94.7%) did
not pass: 407 scored 0 and 90 carry no recorded score at all." Under the
lenient parse the 90 are scored (83 zeros, 7 passes), giving:

| | as submitted | corrected |
|---|---|---|
| at exactly 50 steps | 525 | 525 |
| did not pass | 497 (94.7%) | 490 (93.3%) |
| scored 0 | 407 | 490 |
| no recorded score | 90 | 0 |
| passed at the cap | 28 | 35 |

The claim survives; the "no recorded score" clause was an artefact of our
parser. The arXiv version uses the corrected column and says so in a footnote.

Section 2, the pilot buckets. The buckets were assigned on a matrix that
under-read 3 of 18 bundles. With all 18 read, five of the 29 pilot tasks change
label: `AcceptMeetingTask`, `CheckEventTimeTask`,
`GoogleMapsAlibabaSouthNeighborTask`, `TakeSelfieTask` are no longer
all-models-pass, and `SMSManagement` is no longer all-models-fail. The
measured flip rates do not depend on the labels; the sentence "controls behave
(`all_fail` 0/10, `all_pass` 1/9)" should say the labels came from 15 of 18
bundles, or be recomputed on the corrected buckets. The arXiv version takes the
first option: a footnote in Section 2 names the five tasks.

## Not done

- Opening the S4 runs by hand. The audit's own retained screenshots cover 40
  runs (`data/screenshots_retained.txt`); the published bundles carry no
  screenshots at all, so for published runs S4 is a statement about what a
  reader *cannot* do, not a queue of things to check.
- Any other benchmark. This is the MobileWorld pass; the same six signals are
  the template for the next one.
