"""Corporacion Favorita loader: builds the same long panel schema as
`load_m5.build_demand_price_panel`, so the frozen Sentinel / Investigator /
LLM agent run on it unchanged.

Source: Kaggle "favorita-grocery-sales-forecasting" (train.csv, items.csv,
stores.csv, holidays_events.csv), extracted under data/raw/favorita/.

COLUMN MAPPING (documented choices -- see docs/project_changelog.md too):
  id          "<item_id>_<store_id>"
  item_id     str(item_nbr)
  dept_id     items.family (33 families, e.g. GROCERY I, BEVERAGES, PRODUCE)
  cat_id      perishable group from items.perishable: "PERISHABLE" /
              "NON_PERISHABLE". Chosen because it is provided by the data
              (not an invented grouping) and is coarser than family. Using
              family for both dept_id and cat_id would make every cat_id
              slice identical to a dept_id slice, so the injector could drift
              "the same rows" under two different labels and strict scoring
              would mark the identical answer wrong. The loader verifies that
              perishable is constant within each family (so family ->
              perishable group is a true hierarchy) and fails loudly if not.
  store_id    str(store_nbr)  (54 stores)
  state_id    stores.state    (Ecuadorian province, 16 values)
  date        daily
  sales       train.unit_sales, NEGATIVE values (returns) clipped to 0. Note
              unit_sales is fractional for items sold by weight; M5's sales
              are integers.
  wday        M5 convention (1=Saturday ... 7=Friday), so is_weekend matches.
  is_weekend  wday in (1, 2), same as M5.
  event_flag  1 if a holiday/event applies to that store on that date:
              holidays_events rows of type Holiday/Transfer/Additional/
              Bridge/Event that were not transferred away; National applies
              to all stores, Regional to stores in that state, Local to
              stores in that city. ("Work Day" rows are not events.)
  onpromotion EXTRA column (Favorita-specific), 0/1. Only observed on rows
              with recorded sales; 0 elsewhere. Not used by the pipeline.

NEUTRAL FILLS for M5 columns Favorita does not have:
  sell_price  1.0 for every row (Favorita publishes no prices)
  revenue     sales * sell_price (= sales)
  snap        0 (no SNAP programme in Ecuador)

ZERO SALES: Favorita's train.csv omits item-store-days with no sales. The
panel is the full (item, store) x date grid for every pair with at least
one recorded sale in the window, zero-filled. As in M5, a zero cannot be
told apart from "not stocked that day".

WINDOW / SAMPLE (same logic as M5): the last `n_last_days` = 913 days
(~24-month reference + 6-month holdout), and a stratified sample of
`max_items // n_families` items per family (all of a family's items if it
has fewer), drawn with `random_state`, from items with at least one
recorded sale in the window.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

FAV_DIR = "data/raw/favorita"
CACHE = "data/raw/favorita_panel_cache.parquet"
EVENT_TYPES = {"Holiday", "Transfer", "Additional", "Bridge", "Event"}
CHUNK = 5_000_000


def _extract(data_dir):
    """Unpack *.csv.7z next to themselves if the csv is missing."""
    import py7zr
    for f in os.listdir(data_dir):
        if f.endswith(".csv.7z") and not os.path.exists(os.path.join(data_dir, f[:-3])):
            print(f"extracting {f} ...", flush=True)
            with py7zr.SevenZipFile(os.path.join(data_dir, f)) as z:
                z.extractall(data_dir)


def _read_train(path, start, items=None):
    """Chunked read of train.csv rows on/after `start` (ISO date string),
    optionally restricted to `items`."""
    out = []
    for ch in pd.read_csv(path, usecols=["date", "store_nbr", "item_nbr", "unit_sales", "onpromotion"],
                          dtype={"store_nbr": "int16", "item_nbr": "int32", "unit_sales": "float32"},
                          chunksize=CHUNK):
        ch = ch[ch["date"] >= start]
        if items is not None:
            ch = ch[ch["item_nbr"].isin(items)]
        if len(ch):
            out.append(ch)
    return pd.concat(out, ignore_index=True)


def build_favorita_panel(data_dir: str = FAV_DIR, n_last_days: int = 913, max_items: int = 200,
                         random_state: int = 42, cache: str | None = CACHE, report: dict | None = None):
    """Return the Favorita panel in M5 schema (+ `onpromotion`). `report`,
    if given, is filled with the counts the changelog documents."""
    if cache and os.path.exists(cache):
        df = pd.read_parquet(cache)
        df.attrs["dataset"] = "favorita"
        return df
    report = {} if report is None else report
    _extract(data_dir)
    items = pd.read_csv(os.path.join(data_dir, "items.csv"))
    stores = pd.read_csv(os.path.join(data_dir, "stores.csv"))
    hol = pd.read_csv(os.path.join(data_dir, "holidays_events.csv"), parse_dates=["date"])

    # cat_id = perishable group; must be constant within family
    per_family = items.groupby("family")["perishable"].nunique()
    mixed = per_family[per_family > 1]
    if len(mixed):
        raise ValueError(f"perishable varies within families {mixed.index.tolist()}; "
                         "family -> perishable group is not a hierarchy, choose another cat_id")
    fam_to_cat = items.groupby("family")["perishable"].first().map({1: "PERISHABLE", 0: "NON_PERISHABLE"})

    # window: last n_last_days of the data
    train_path = os.path.join(data_dir, "train.csv")
    last = None
    for ch in pd.read_csv(train_path, usecols=["date"], chunksize=CHUNK):
        m = ch["date"].max()
        last = m if last is None or m > last else last
    end = pd.Timestamp(last)
    start = end - pd.Timedelta(days=n_last_days - 1)
    report.update(window_start=str(start.date()), window_end=str(end.date()))

    # pass 1: items with >=1 recorded sale in the window, then stratified sample by family
    seen = _read_train(train_path, str(start.date()))[["item_nbr"]].drop_duplicates()
    cand = items[items["item_nbr"].isin(seen["item_nbr"])]
    n_fam = cand["family"].nunique()
    per = max(1, max_items // n_fam) if max_items else None
    sampled = cand.groupby("family", group_keys=False).apply(
        lambda g: g.sample(min(len(g), per), random_state=random_state)) if per else cand
    report.update(n_families=int(n_fam), items_per_family_quota=per, n_items=int(len(sampled)),
                  families_below_quota=sorted(cand.groupby("family").size().loc[lambda s: s < (per or 0)].index))

    # pass 2: rows for sampled items, zero-filled onto the (pair x date) grid
    tr = _read_train(train_path, str(start.date()), set(sampled["item_nbr"]))
    tr["date"] = pd.to_datetime(tr["date"])
    report["n_negative_sales_clipped"] = int((tr["unit_sales"] < 0).sum())
    tr["unit_sales"] = tr["unit_sales"].clip(lower=0)
    tr["onpromotion"] = tr["onpromotion"].map({True: 1, False: 0, "True": 1, "False": 0}).fillna(0).astype("int8")
    pairs = tr[["item_nbr", "store_nbr"]].drop_duplicates()
    dates = pd.DataFrame({"date": pd.date_range(start, end, freq="D")})
    grid = pairs.merge(dates, how="cross")
    df = grid.merge(tr, on=["item_nbr", "store_nbr", "date"], how="left")
    report.update(n_pairs=int(len(pairs)), n_rows=int(len(df)),
                  share_zero_filled=float(df["unit_sales"].isna().mean()))
    df["unit_sales"] = df["unit_sales"].fillna(0.0)
    df["onpromotion"] = df["onpromotion"].fillna(0).astype("int8")

    df = df.merge(items[["item_nbr", "family"]], on="item_nbr").merge(
        stores[["store_nbr", "city", "state"]], on="store_nbr")

    # event_flag per store-date
    h = hol[hol["type"].isin(EVENT_TYPES) & ~hol["transferred"].astype(bool)]
    nat = set(h.loc[h["locale"] == "National", "date"])
    reg = set(zip(h.loc[h["locale"] == "Regional", "date"], h.loc[h["locale"] == "Regional", "locale_name"]))
    loc = set(zip(h.loc[h["locale"] == "Local", "date"], h.loc[h["locale"] == "Local", "locale_name"]))
    ev = df["date"].isin(nat)
    ev |= pd.Series(list(zip(df["date"], df["state"])), index=df.index).isin(reg)
    ev |= pd.Series(list(zip(df["date"], df["city"])), index=df.index).isin(loc)

    out = pd.DataFrame({
        "item_id": df["item_nbr"].astype(str),
        "dept_id": df["family"].astype(str),
        "cat_id": df["family"].map(fam_to_cat).astype(str),
        "store_id": df["store_nbr"].astype(str),
        "state_id": df["state"].astype(str),
        "date": df["date"],
        "sales": df["unit_sales"].astype(float),
        "onpromotion": df["onpromotion"],
        "event_flag": ev.astype(int).to_numpy(),
    })
    out.insert(0, "id", out["item_id"] + "_" + out["store_id"])
    out["wday"] = ((out["date"].dt.dayofweek + 2) % 7 + 1).astype(int)  # M5: 1=Sat ... 7=Fri
    out["is_weekend"] = out["wday"].isin([1, 2]).astype(int)
    out["sell_price"] = 1.0
    out["snap"] = 0
    out["revenue"] = out["sales"] * out["sell_price"]
    out = out[["id", "item_id", "dept_id", "cat_id", "store_id", "state_id", "date", "wday", "sales",
               "sell_price", "snap", "is_weekend", "event_flag", "revenue", "onpromotion"]]
    out = out.sort_values(["id", "date"]).reset_index(drop=True)
    report.update(n_stores=int(out["store_id"].nunique()), n_states=int(out["state_id"].nunique()),
                  cat_values=sorted(out["cat_id"].unique()), event_day_share=float(out["event_flag"].mean()))
    if cache:
        out.to_parquet(cache, index=False)
    out.attrs["dataset"] = "favorita"
    return out


if __name__ == "__main__":
    import json
    rep = {}
    panel = build_favorita_panel(report=rep)
    print(panel.shape)
    print(json.dumps(rep, indent=1, default=str))
