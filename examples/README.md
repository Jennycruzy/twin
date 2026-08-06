# examples

Output from real runs against a live stack. Nothing here is written by hand.

Every `.txt` file is the captured stdout of a command that actually executed, with a header
naming the commit it came from and when it ran. `ops/capture-examples.sh` produces them and
`make examples` invokes it. If a command fails, its artifact is left as it was rather than
being overwritten with the failure — so a file here is always the output of a run that
succeeded, and the header says which one.

Deliberately not regenerated on a schedule. These are snapshots of a commit; the history
below is the thing that accumulates.

## reports/

| file | what produced it |
|---|---|
| `verify-estate.txt` | `make verify-estate` — the thirteen checks that prove the demo estate is real, run against DataHub |
| `fragility-scorecard.txt` | `make score` — the knockout sweep and fragility ranking across every dataset |
| `estate-graph.txt` | `make graph` — what Stage 1 read over MCP, summarised, with the fingerprint it was keyed on |

## verification/

One file per scenario, each the full transcript of `make run`: the predicted timeline, the
fault executed for real in a shadow schema, the dbt build that followed, the consumer queries
re-run against the result, and the grading of prediction against observation.

The scorecards are printed as they came out. A false alarm or a miss is named in the file
with the error the warehouse returned, because a scorecard that only shows its wins is not
evidence of anything.

## history/

`nightly.jsonl` and `fragility.jsonl` are append-only, one line per run that genuinely
happened — the nightly writes only after a read succeeds, so a failed night leaves no row
rather than a row asserting something unchecked. `README.md` there is a rendered view of the
two, produced by `ops/render-history.py`, which computes nothing and adds nothing.

This is the one claim in the project that cannot be manufactured after the fact, which is why
the raw files are kept alongside the rendering.

## incidents/, repair-prs/

Empty. Stage 5 raises incidents and `make incidents` prints what it did, but no transcript is
committed here yet. Repair PRs are not built — see *Limitations* in the top-level README.
