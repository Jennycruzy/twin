# Scoring: how fragility is calculated, and what it does not mean

A fragility score is a weighted sum of five measured components. The weights live in
`config/scoring.yml` and every component is printed beside the total by `make score`, so a
ranking can be argued with on its parts rather than accepted or rejected whole.

This document exists because the score is the most falsifiable thing Twin produces and the
easiest thing to fake. Everything below is derived from the estate; nothing is a judgement
Twin brought with it.

## The test the model has to pass

The estate contains structural weaknesses that are not annotated anywhere in this
repository. The model is correct to the extent that it finds them without being told, and
the interesting case is where **size and danger disagree**.

`raw_pg.orders` has the widest reach of any asset in the estate — knocking it out takes 16
datasets and 22 consumers. `raw_pg.fx_rates` reaches slightly less: 15 and 21. A scorer that
ranks by fan-out returns orders.

Twin ranks fx_rates first and orders fourth:

```
   #  ASSET                             SCORE   blast  expos  recov  conce  blind   BLAST
   1  raw_pg.fx_rates                    77.9    0.95   0.95   1.00   0.29   0.12   15+21
   2  staging.stg_fx_rates               77.4    0.92   0.95   1.00   0.30   0.12   14+21
   3  intermediate.int_orders_enriched   58.6    0.87   0.93   0.33   0.35   0.00   12+21
   4  raw_pg.orders                      55.1    1.00   1.00   0.00   0.26   0.12   16+22
```

Orders wins on blast and on exposure — the two components most scorers would use — and loses
on recovery, because it has a standby and the FX feed does not. That is the entire
difference, and it is the difference that matters operationally: losing orders is survivable
because the data can be served from somewhere else, and losing fx_rates is not.

## The components

Each is measured per asset, then combined. Blast and exposure are min-max normalised across
the estate; the rest are already bounded and are used as measured, because normalising them
would make "least bad here" look safe rather than merely comparatively better.

### blast — 0.25

What the knockout sweep says falls over: datasets and consumers together.

The sweep does not count graph edges. It runs the propagation model once per asset with a
`drop_asset` fault and reads the blast radius off the resulting timeline — the same model
Stage 4 executes against a real warehouse. This is deliberate: it means a fragility score is
a claim shadow execution can be pointed at. A number derived from adjacency could not be
checked against anything.

Deliberately not the largest weight. A wide blast radius with a standby behind it is less
dangerous than a narrow one without.

### exposure — 0.25

Query executions that really happened against the assets in the blast radius.

These counts come from the workload that `make estate` genuinely runs — 3,458 executions,
none of them invented. They separate a mart three dashboards refresh hourly from an
intermediate model nobody reads.

**This is the one component that does not come over MCP.** DataHub holds the usage
statistics and the MCP server exposes no tool that returns them, so they are read through the
SDK. The alternatives were to drop usage-weighting entirely or to invent counts; the first
makes the ranking worse and the second is the dishonesty this project exists to avoid. See
`twin/score/usage.py`.

### recovery — 0.25

Whether the estate can serve this from anywhere else.

Replication and fallbacks are declared on the sources that land data, not on the models built
from them, so a model inherits the exposure of everything beneath it. An asset resting
entirely on unreplicated sources with no declared fallback scores 1.0; one whose sources are
all replicated scores 0.0; a declared fallback halves the penalty. An asset with nothing
declared either way scores 0.5 — neither credited nor condemned for metadata that does not
exist.

This is the component that makes the model disagree with fan-out, and it is the component
most dependent on metadata being present. On an estate where nobody records replication, it
degenerates to a constant and the ranking collapses toward blast. That is a real limitation,
not a hypothetical one.

### concentration — 0.15

How few people can fix the wreckage, and how much of it belongs to nobody.

Two failure modes with one consequence. An incident spread across five owners is a bad night;
the same incident owned by one person is a bus factor with a deadline; one owned by nobody
has no one to page at all. Scored as half owner-concentration (`1/owners`) and half the
unowned share of the blast radius.

### blindness — 0.10

How long the damage sits before it reaches something a person looks at.

Taken from the predicted timeline: the offset of the first consumer event. Slow-refreshing
assets score higher, because a mart that quietly serves yesterday's numbers until tomorrow
morning is worse than one that fails loudly at once. Capped at a 24-hour horizon so that
assets nothing consumes cannot dominate the ranking.

## What a score does not mean

- **Scores are positions within one estate, not absolute values.** Blast and exposure are
  normalised against this platform's own maximum, so the same asset in a larger estate would
  score differently. Two estates' numbers are not comparable.
- **The weights are a claim, and reasonable people would choose differently.** They are in
  `config/scoring.yml` precisely so that disagreement is a config change rather than an
  argument with a black box.
- **The model has been fitted to one estate.** It finds the weaknesses planted in this one.
  That is evidence it is not arbitrary; it is not evidence it generalises.
- **Fragility is not importance.** A tier-1 mart that is well replicated and widely owned can
  score below an obscure feed that nothing protects. That is the intended behaviour, and it
  is worth stating because a low score is not permission to ignore an asset.
