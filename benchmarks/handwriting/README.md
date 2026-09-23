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

## Status

The eval set is empty: nothing has been confirmed on this machine yet. The
baseline cannot be scored until a page's lines are confirmed, which is the
point of the confirmation step rather than an obstacle to work around.

The miss this exists for, from Lab 1 calculations page 3 at `mps`:

```
want  v_b = v_a → G = 10
got   u_b = v_a → v = 0
```
