"""Lite scorer == veckit.score on the four ranked T1 metrics (synthetic data, runs in seconds).

    python -m pytest -q test_lite_score.py      (or just: python test_lite_score.py)
"""
import sys
from pathlib import Path

import anndata as ad
import numpy as np
from scipy import sparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lite_score import RANKED, lite_score  # noqa: E402


def _stage(rng, n, genes, shift, types):
    lam = np.exp(rng.normal(0, 1, genes.size) + shift)
    X = np.log1p(rng.poisson(lam, size=(n, genes.size)).astype(np.float32))
    a = ad.AnnData(sparse.csr_matrix(X))
    a.var_names = genes
    a.obs["celltype"] = rng.choice(types, n)
    return a


def test_identical(tmp=None):
    tmp = Path(tmp or Path(__file__).resolve().parent / "_test_tmp")
    tmp.mkdir(exist_ok=True)
    rng = np.random.default_rng(0)
    genes = np.array([f"g{i}" for i in range(400)])
    types = np.array(["a", "b", "c"])
    shift = np.zeros(genes.size)
    shift[:40] = 1.0
    shift[40:80] = -1.0
    paths = {}
    for name, n, s in (("ref", 600, 0), ("target", 600, shift), ("pred", 500, 0.6 * shift)):
        paths[name] = tmp / f"{name}.h5ad"
        _stage(rng, n, genes, s, types).write_h5ad(paths[name])
    lite = lite_score(paths["pred"], paths["target"], paths["ref"], block=37)["metrics"]
    from veckit import score
    full = score(task="T1", input=str(paths["pred"]), target=str(paths["target"]),
                 reference=str(paths["ref"]))["metrics"]
    for k in RANKED + ("_n_up", "_n_dn"):
        assert lite[k] == full[k], (k, lite[k], full[k])
    print("identical:", {k: lite[k] for k in RANKED})


if __name__ == "__main__":
    test_identical()
