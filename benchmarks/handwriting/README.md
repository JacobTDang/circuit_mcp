# Handwriting recognition eval

UniMERNet is trained mostly on printed formulas. Segmentation finds the right
lines on a scanned page; what comes back for each line is often wrong. This is
how a candidate model is judged before it replaces the current one.

## What is scored

Exact match is the headline, and three error classes are counted separately,
because they are not equally bad:

| class | why it is counted on its own |
|---|---|
| sign | a lost minus sign makes a derivation wrong, not untidy |
| subscript | `v_b` read as `v_a` changes which node the line is about |
| digit | a changed result is a changed answer |

A candidate is adopted only when it reads **more** lines exactly **without**
losing more signs or subscripts. A model that gains exact matches while
dropping a minus sign is not an improvement for this work.

One thing the classes do not catch: a changed stem, such as the `G → v` in the
Lab 1 miss below. That shows up only in exact match, and every miss is printed
in full so it can be read rather than inferred from counts.

## The labels

Confirmed transcriptions, and nothing else. An unconfirmed transcription is the
model's own output, and scoring a model against its own output measures
nothing.

```console
# rebuild the eval set from what has been confirmed in the command centre
uv run python benchmarks/handwriting/run.py --from-store
```

`manifest.json` is committed; the crops it names are not. They are a student's
own coursework and live beside the manifest in this directory, untracked.

## Running it

```console
uv run python benchmarks/handwriting/run.py --model models/unimernet_small
```

Each run writes `results/<model>-<stamp>.json` so one candidate can be held
against the last. Everything runs offline against the local OCR worker.

## Candidates, in order of cost

1. `unimernet_base` — same package, no new dependency.
2. Crop preprocessing — deskew, contrast, padding.
3. A CROHME-trained model such as PosFormer — a new dependency, which needs
   sign-off before it is added.

## The eval set

Fourteen formula lines from Lab 1 calculations page 3, rendered at 200 dpi and
segmented by the page tool. The two prose headings on that page are left out:
this is a formula recogniser, and scoring it on prose measures something it
never claimed to do.

The labels were read off the page and confirmed line by line before they were
written down. They spell what the page spells -- uppercase `V`, as handwritten
-- so a model that returns `v` is counted as not reading what is there.

The crops are the student's own coursework and are not committed. Each sample
carries the box it was cut from, so the set rebuilds from the page:

```console
pdftoppm -r 200 -png -f 3 -l 3 ee230_lab_calculations.pdf page
uv run python benchmarks/handwriting/run.py --cut-crops page-3.png
```

## Baseline

`unimernet_small`, the model in `models/`, on 2026-09-23:

| exact | sign | subscript | digit |
|---|---|---|---|
| **0 / 14** | 0 | 60 | 40 |

Not one line of fourteen comes back exactly. No minus sign was lost, which is
the one piece of good news in it. The miss this was filed for is line 10:

```
want  V_{b} = V_{a} \rightarrow G = 10
got   u_{b} = v_{a} \rightarrow v = 0
```

Two failures repeat across the set and are worth knowing before picking a
candidate: `V` is read as `v` almost everywhere, and the letter `o` in `V_o` is
read as the digit `0`. A candidate that fixes only those would move the exact
count a long way without understanding the handwriting any better, so read the
misses, not just the totals.
