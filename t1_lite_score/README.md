# T1 lite score: the four ranked Task 1 metrics, identical to veckit 0.1.2, in ~7 GB instead of ~40 GB

`veckit.score(task="T1", ...)` peaked at 38–47 GB for us on the released stages. That's enough to get OOM-killed on a 32 or 64 GB machine, especially with anything else running. Two steps cause it:

- `de_genes` runs one `mannwhitneyu` over the whole dense cells × 32,285 genes matrix;
- `train_frozen_probe` fits a classifier on the dense target, for `composition_JSD`. That is a constraint metric, not a ranked one.

`lite_score.py` computes only the four ranked T1 metrics (`de_score`, `de_direction`, `mmd_u`, `variogram`). It uses veckit's own loaders and metric functions. The one change: the Mann-Whitney test runs over blocks of genes rather than all genes at once. The test is per gene, so the p-values are the same. Benjamini–Hochberg and the logFC floor are then applied to the full vector, exactly as in veckit. No probe is trained.

| | full `veckit.score` | `lite_score.py` |
|---|---|---|
| E8.5 → E9.5 fold, 5,000-cell prediction | 38–47 GB peak | 7.3 GB peak RSS; 5.5 min on a busy 12-core machine |
| metrics | full panel | 4 ranked metrics + DE gene counts |

## Use

```bash
pip install veckit==0.1.2
python lite_score.py --input pred.h5ad --target E9.5_RNA.h5ad --reference E8.5_RNA.h5ad
python lite_score.py --input pred.h5ad --target E9.5_RNA.h5ad --reference E8.5_RNA.h5ad --check-full
#   --check-full also runs veckit.score and compares (needs the full memory)
```

Output is the same JSON shape as veckit (`meta`, `metrics`), with the same rounding. `--seed` is veckit's metric seed and defaults to 0. Use the same seed you compare against.

The script calls veckit's internal helpers (`score_h5ad._prediction`, `common.core_metrics`), which are not a public API and may change in another veckit version. That is why it is pinned to 0.1.2; run `test_lite_score.py` after any upgrade.

## Checked

- `test_lite_score.py`: synthetic data, block size 37, all four metrics and `_n_up`/`_n_dn` identical to `veckit.score`. Runs in seconds. The full veckit run inside it prints sklearn `ConvergenceWarning`s from its probe; ignore them.
- Real data, 3,000-cell subsample of E8.5 / E9.5, `--check-full`: identical on every reported field.
- Full-size E8.5 → E9.5: DE gene counts (27 up, 28 down) match what full veckit reported on the same fold.

## Related

veckit PR [#2](https://github.com/aristoteleo/veckit/pull/2) ("Memory-safe and interruptible", open since August) makes veckit itself memory-safe (sparse data, chunked Mann-Whitney) and keeps the full panel. If it is merged, use that. This script is for the released `pip install veckit` as it is: no fork or patch, and the ranked numbers only.

## Not covered

Constraint metrics (`composition_JSD`, `pb_rel_err`, ...) and `energy_distance`. For those, use full veckit. To turn raw metrics into the 0–100 board scale you still need the floor and ceiling for your fold; see the Evaluation page.

MIT licence.
