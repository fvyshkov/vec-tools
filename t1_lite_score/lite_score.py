"""Memory-light, exact Task 1 scoring with veckit.

`veckit.score(task="T1", ...)` computes the whole metric panel. Two steps dominate its memory:

* `de_genes(target, reference)` runs `scipy.stats.mannwhitneyu` on the full dense cells x genes matrices
  (~40k cells x ~32k genes on the real stages) in one call -- tens of GB;
* `train_frozen_probe` fits a classifier on the dense target for `composition_JSD`, which is a constraint
  metric, not one of the four ranked ones.

This script computes the four ranked T1 metrics (`de_score`, `de_direction`, `mmd_u`, `variogram`) with
veckit's own loaders and metric functions, and only replaces `mannwhitneyu` over all genes at once by the
same test over blocks of genes. The test is per gene, so the p-values are identical; Benjamini-Hochberg and
the logFC floor are then applied to the full vector exactly as veckit does. No probe is trained.

    python lite_score.py --input pred.h5ad --target target.h5ad --reference reference.h5ad [--seed 0]
    python lite_score.py ... --check-full      # also run veckit.score and compare (needs the full memory)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy import sparse
from scipy.stats import mannwhitneyu

import score_h5ad as _vk                     # veckit's file loader: same validation and preprocessing
import common.core_metrics as _cm            # veckit's metric functions

RANKED = ("de_score", "de_direction", "mmd_u", "variogram")


def _block_dense(X, j0, j1):
    """Dense float32 block of columns j0:j1 (X dense, CSR or CSC)."""
    B = X[:, j0:j1]
    return B.toarray() if sparse.issparse(B) else np.asarray(B)


def de_genes_blocked(X_cond, X_ref, alpha=0.05, min_lfc=_cm.MIN_LFC, min_cells=10, block=1000):
    """veckit `de_genes`, with the Mann-Whitney test run over blocks of genes instead of all at once."""
    lfc = _cm.pseudobulk(X_cond) - _cm.pseudobulk(X_ref)
    if X_cond.shape[0] < min_cells or X_ref.shape[0] < min_cells:
        return np.array([], int), np.array([], int), lfc
    A = X_cond.tocsc() if sparse.issparse(X_cond) else X_cond
    B = X_ref.tocsc() if sparse.issparse(X_ref) else X_ref
    G = X_cond.shape[1]
    p = np.empty(G, dtype=np.float64)
    with np.errstate(all="ignore"):
        for j in range(0, G, block):
            k = min(j + block, G)
            p[j:k] = mannwhitneyu(_block_dense(A, j, k), _block_dense(B, j, k), axis=0, alternative="two-sided")[1]
    p = np.nan_to_num(p, nan=1.0)
    order = np.argsort(p)
    m = len(p)
    below = p[order] <= alpha * (np.arange(1, m + 1) / m)
    kk = int(np.max(np.where(below)[0]) + 1) if below.any() else 0
    sig = order[:kk]
    if min_lfc > 0:
        sig = sig[np.abs(lfc[sig]) >= min_lfc]
    return sig[lfc[sig] > 0], sig[lfc[sig] < 0], lfc


def lite_score(input, target, reference, seed=0, block=1000):
    """The four ranked T1 metrics, rounded exactly as veckit's `score_task1_v2` reports them."""
    ref_X, _, _, ref_A = _vk._prediction(Path(reference), None, need_coords=False, allow_reorder=False)
    genes = [str(g) for g in ref_A.var_names]
    del ref_A
    true_X = _vk._prediction(Path(target), genes, need_coords=False, allow_reorder=False)[0]
    pred_X, _, _, pred_A = _vk._prediction(Path(input), genes, need_coords=False, allow_reorder=False)
    n_pred = int(pred_A.n_obs)
    del pred_A

    orig = _cm.de_genes
    _cm.de_genes = lambda Xc, Xr, alpha=0.05, min_lfc=_cm.MIN_LFC, min_cells=10: \
        de_genes_blocked(Xc, Xr, alpha, min_lfc, min_cells, block)
    try:
        de = _cm.de_score(pred_X, true_X, ref_X)
    finally:
        _cm.de_genes = orig
    metrics = {
        "de_score": round(de["score"], 4) if np.isfinite(de["score"]) else None,
        "de_direction": round(_cm.de_direction(pred_X, true_X, ref_X), 4),
        "mmd_u": round(_cm.mmd_unbiased(pred_X, true_X, seed=seed), 5),
        "variogram": round(_cm.variogram_score(pred_X, true_X, seed=seed), 6),
        "_de_raw": round(de["raw"], 4) if np.isfinite(de["raw"]) else None,
        "_n_up": int(de["n_up"]), "_n_dn": int(de["n_dn"]),
    }
    meta = {"task": "T1", "truth_cells": int(true_X.shape[0]), "prediction_cells": n_pred, "genes": len(genes),
            "seed": seed, "note": "ranked metrics only; constraint metrics (composition_JSD etc.) not computed"}
    return {"meta": meta, "metrics": metrics}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="prediction .h5ad")
    ap.add_argument("--target", required=True, help="the stage you score against (.h5ad)")
    ap.add_argument("--reference", required=True, help="the preceding stage, DE reference (.h5ad)")
    ap.add_argument("--seed", type=int, default=0, help="veckit metric seed (default 0, as the board)")
    ap.add_argument("--block", type=int, default=1000, help="genes per Mann-Whitney block")
    ap.add_argument("--check-full", action="store_true", help="also run veckit.score and compare")
    a = ap.parse_args(argv)
    res = lite_score(a.input, a.target, a.reference, seed=a.seed, block=a.block)
    if a.check_full:
        from veckit import score
        full = score(task="T1", input=a.input, target=a.target, reference=a.reference, seed=a.seed)["metrics"]
        res["full_veckit"] = {k: full.get(k) for k in res["metrics"]}
        res["identical"] = all(res["metrics"][k] == full.get(k) for k in res["metrics"])
    print(json.dumps(res, indent=2))
    return 0 if res.get("identical", True) else 1


if __name__ == "__main__":
    sys.exit(main())
