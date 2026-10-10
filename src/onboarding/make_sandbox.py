"""Build the onboarding sandboxes (Phase 8): raw Favorita files only.

outputs/onboarding/sandbox/named/     original file names, columns, values
outputs/onboarding/sandbox/obscured/  file_1..file_5, columns col_a.., family /
                                      city / state / holiday text as opaque codes
outputs/onboarding/private_mapping.json   obscured -> original (scorer only;
                                      deliberately OUTSIDE the sandbox)

train is sampled by ITEM (every row of N random items, all dates), so the
per-item/store date structure -- including the days Favorita omits because
nothing sold -- is preserved.
"""
from __future__ import annotations

import json
import os
import string

import numpy as np
import pandas as pd

SRC = "data/raw/favorita"
OUT = "outputs/onboarding"
N_ITEMS = 150
SEED = 2026
FILES = ["train.csv", "items.csv", "stores.csv", "holidays_events.csv", "transactions.csv"]


def _codes(prefix, values):
    vals = sorted(set(values))
    return {v: f"{prefix}{i:03d}" for i, v in enumerate(vals, 1)}


def main():
    named = f"{OUT}/sandbox/named"
    obsc = f"{OUT}/sandbox/obscured"
    os.makedirs(named, exist_ok=True)
    os.makedirs(obsc, exist_ok=True)

    items = pd.read_csv(f"{SRC}/items.csv")
    rng = np.random.default_rng(SEED)
    sample = set(rng.choice(items["item_nbr"].to_numpy(), N_ITEMS, replace=False).tolist())
    parts = [ch[ch["item_nbr"].isin(sample)] for ch in pd.read_csv(f"{SRC}/train.csv", chunksize=5_000_000)]
    frames = {
        "train.csv": pd.concat(parts, ignore_index=True),
        "items.csv": items,
        "stores.csv": pd.read_csv(f"{SRC}/stores.csv"),
        "holidays_events.csv": pd.read_csv(f"{SRC}/holidays_events.csv"),
        "transactions.csv": pd.read_csv(f"{SRC}/transactions.csv"),
    }
    for f, d in frames.items():
        d.to_csv(f"{named}/{f}", index=False)

    # obscured: one opaque code per distinct original column name, across files
    all_cols = sorted({c for d in frames.values() for c in d.columns})
    col_map = {c: "col_" + (string.ascii_lowercase[i] if i < 26 else f"z{i}") for i, c in enumerate(all_cols)}
    file_order = list(np.random.default_rng(SEED + 1).permutation(FILES))
    file_map = {f: f"file_{i}.csv" for i, f in enumerate(file_order, 1)}
    st, hol = frames["stores.csv"], frames["holidays_events.csv"]
    family_map = _codes("F", frames["items.csv"]["family"])
    place_map = _codes("P", list(st["city"]) + list(st["state"]) + list(hol["locale_name"]))  # one space for cities/states/"Ecuador"
    desc_map = _codes("H", hol["description"])
    value_maps = {"family": family_map, "city": place_map, "state": place_map,
                  "locale_name": place_map, "description": desc_map}
    for f, d in frames.items():
        o = d.copy()
        for col, m in value_maps.items():
            if col in o:
                o[col] = o[col].map(m)
        o.columns = [col_map[c] for c in o.columns]
        o.to_csv(f"{obsc}/{file_map[f]}", index=False)

    mapping = dict(files={v: k for k, v in file_map.items()}, columns={v: k for k, v in col_map.items()},
                   values={col: {v: k for k, v in m.items()} for col, m in value_maps.items()},
                   n_items_sampled=N_ITEMS, seed=SEED, train_rows=int(len(frames["train.csv"])))
    json.dump(mapping, open(f"{OUT}/private_mapping.json", "w"), indent=1)
    print({f: len(d) for f, d in frames.items()}, "| obscured files:", file_map)


if __name__ == "__main__":
    main()
