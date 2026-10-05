"""
Data layer for the drift-detection pipeline.

IMPORTANT — HONEST DATA-SOURCE NOTE (read this before trusting any number
downstream): the build spec for this project calls for the real Kaggle
M5 Forecasting - Accuracy dataset. This code was written inside a sandboxed
environment whose network egress allow-list does NOT include kaggle.com
(or any mirror that legitimately redistributes the M5 CSVs — GitHub repos
that reference M5 uniformly expect the user to download it from Kaggle
themselves; redistributing Kaggle's competition data outside Kaggle's own
terms is not something to route around by scraping a mirror even if one
existed). Rather than silently fabricate results against data we don't
actually have, or block entirely, this module ships a SYNTHETIC generator,
`build_synthetic_m5_like_panel`, that reproduces M5's exact schema (columns,
dtypes, cardinalities in the right ballpark, hierarchy structure, sparsity,
weekly/annual seasonality, SNAP/event flags, promotional price dips) so
every downstream agent, drift injector, and evaluator runs against
something structurally faithful to M5.

`build_demand_price_panel` is the function name specified in the build
prompt. It is a thin wrapper: if real M5 CSVs are present under
`data_dir` (i.e. someone runs this on their own machine after downloading
them from Kaggle), it uses them for real; otherwise it falls back to the
synthetic generator and prints a loud, unambiguous warning so nobody
mistakes synthetic output for a real-data result. Every number in
README.md and outputs/*.csv produced by this environment is a SYNTHETIC
result and is labeled as such throughout.
"""
from __future__ import annotations

import os
import warnings
import numpy as np
import pandas as pd

CAT_DEPT_MAP = {
    "HOBBIES": ["HOBBIES_1", "HOBBIES_2"],
    "HOUSEHOLD": ["HOUSEHOLD_1", "HOUSEHOLD_2"],
    "FOODS": ["FOODS_1", "FOODS_2", "FOODS_3"],
}
STATE_STORE_MAP = {
    "CA": ["CA_1", "CA_2", "CA_3", "CA_4"],
    "TX": ["TX_1", "TX_2", "TX_3"],
    "WI": ["WI_1", "WI_2", "WI_3"],
}
ALL_STORES = [s for stores in STATE_STORE_MAP.values() for s in stores]
STORE_TO_STATE = {s: st for st, stores in STATE_STORE_MAP.items() for s in stores}

REAL_M5_FILES = [
    "calendar.csv",
    "sell_prices.csv",
    "sales_train_validation.csv",
]


def _real_m5_available(data_dir: str) -> bool:
    return all(os.path.exists(os.path.join(data_dir, f)) for f in REAL_M5_FILES)


def build_synthetic_m5_like_panel(
    n_last_days: int = 913,
    max_items: int = 350,
    random_state: int = 42,
) -> pd.DataFrame:
    """Generate a long panel with the same schema M5 would produce after
    melting, at the given window length and item sample size.

    Structural properties deliberately reproduced (these are what the
    Sentinel/Investigator/Synthesizer actually depend on, so they matter
    more than exact marginal statistics):
      - hierarchy: item_id -> dept_id -> cat_id, store_id -> state_id
      - weekly seasonality (weekend lift) and annual seasonality (a smooth
        yearly cycle plus a Christmas/holiday spike)
      - heavy right-skew / sparsity in daily unit sales (many zeros,
        matching M5's well-known intermittent-demand character)
      - price stickiness with occasional promotional dips
      - SNAP calendar flags per state
    """
    rng = np.random.default_rng(random_state)

    cats = list(CAT_DEPT_MAP.keys())
    # stratified item sampling by cat_id, mirroring max_items behavior in the spec
    items_per_cat = max(1, max_items // len(cats)) if max_items else 4000 // len(cats)
    items = []
    for cat in cats:
        depts = CAT_DEPT_MAP[cat]
        for i in range(items_per_cat):
            dept = depts[i % len(depts)]
            items.append(
                {
                    "item_id": f"{dept}_{i:03d}",
                    "dept_id": dept,
                    "cat_id": cat,
                    "base_demand": rng.gamma(shape=2.0, scale=1.5),
                    "base_price": {
                        "HOBBIES": rng.uniform(4, 40),
                        "HOUSEHOLD": rng.uniform(3, 30),
                        "FOODS": rng.uniform(1, 12),
                    }[cat],
                }
            )
    items_df = pd.DataFrame(items)

    end_date = pd.Timestamp("2016-06-19")
    dates = pd.date_range(end=end_date, periods=n_last_days, freq="D")
    cal = pd.DataFrame({"date": dates})
    cal["wday"] = cal["date"].dt.dayofweek
    cal["is_weekend"] = cal["wday"].isin([4, 5]).astype(int)
    doy = cal["date"].dt.dayofyear
    cal["annual_cycle"] = np.sin(2 * np.pi * (doy - 30) / 365.25)
    cal["is_christmas_window"] = ((cal["date"].dt.month == 12) & (cal["date"].dt.day >= 15)).astype(int)
    for st in STATE_STORE_MAP:
        # ~10 SNAP days/month, deterministic-but-state-specific pattern
        cal[f"snap_{st}"] = ((cal["date"].dt.day + hash(st) % 3) % 3 == 0).astype(int)

    rows = []
    for store in ALL_STORES:
        state = STORE_TO_STATE[store]
        store_mult = rng.uniform(0.8, 1.25)
        # per-item promo schedule: random sparse promo days
        for _, it in items_df.iterrows():
            promo_mask = rng.random(len(cal)) < 0.06
            price_noise = rng.normal(0, 0.01, len(cal))
            price = it["base_price"] * (1 + price_noise)
            price = np.where(promo_mask, price * rng.uniform(0.6, 0.85), price)

            lam = (
                it["base_demand"]
                * store_mult
                * (1 + 0.35 * cal["is_weekend"].to_numpy())
                * (1 + 0.25 * cal["annual_cycle"].to_numpy())
                * (1 + 0.9 * cal["is_christmas_window"].to_numpy())
                * (1 + 0.4 * promo_mask.astype(float))
                * (1 + 0.15 * cal[f"snap_{state}"].to_numpy())
            )
            lam = np.clip(lam, 0.01, None)
            sales = rng.poisson(lam)

            df = pd.DataFrame(
                {
                    "id": f"{it['item_id']}_{store}",
                    "item_id": it["item_id"],
                    "dept_id": it["dept_id"],
                    "cat_id": it["cat_id"],
                    "store_id": store,
                    "state_id": state,
                    "date": cal["date"].values,
                    "wday": cal["wday"].values,
                    "sales": sales,
                    "sell_price": price,
                    "snap": cal[f"snap_{state}"].values,
                    "is_weekend": cal["is_weekend"].values,
                    "event_flag": cal["is_christmas_window"].values,
                }
            )
            rows.append(df)

    panel = pd.concat(rows, ignore_index=True)
    panel["revenue"] = panel["sales"] * panel["sell_price"]
    panel = panel.sort_values(["id", "date"]).reset_index(drop=True)
    return panel


def build_demand_price_panel(
    data_dir: str = "data/raw",
    n_last_days: int = 913,
    max_items: int = 350,
    random_state: int = 42,
) -> pd.DataFrame:
    """Build the long demand/price panel used throughout the pipeline.

    Uses real M5 CSVs from `data_dir` if present (download them yourself
    from the Kaggle M5 Forecasting - Accuracy competition and place
    calendar.csv / sell_prices.csv / sales_train_validation.csv there);
    otherwise falls back to a schema-faithful synthetic generator and
    warns loudly. See module docstring for why.
    """
    if _real_m5_available(data_dir):
        return _build_from_real_m5(data_dir, n_last_days, max_items, random_state)

    warnings.warn(
        "\n"
        "==================================================================\n"
        "REAL M5 DATA NOT FOUND under '%s'.\n"
        "Falling back to a SYNTHETIC, schema-faithful panel generator.\n"
        "Every evaluation number produced from this run is SYNTHETIC, not\n"
        "measured against the real Kaggle M5 dataset. To use real data,\n"
        "download calendar.csv / sell_prices.csv / sales_train_validation.csv\n"
        "from the Kaggle M5 Forecasting - Accuracy competition and place\n"
        "them in that directory.\n"
        "==================================================================" % data_dir,
        stacklevel=2,
    )
    return build_synthetic_m5_like_panel(n_last_days=n_last_days, max_items=max_items, random_state=random_state)


def _build_from_real_m5(data_dir: str, n_last_days: int, max_items, random_state: int) -> pd.DataFrame:
    """Real M5 loader path (exercised only when the CSVs are actually present,
    e.g. on a machine with internet access to Kaggle). Not exercised in this
    sandboxed build/eval run — see module docstring."""
    rng = np.random.default_rng(random_state)
    cal = pd.read_csv(os.path.join(data_dir, "calendar.csv"), parse_dates=["date"])
    prices = pd.read_csv(os.path.join(data_dir, "sell_prices.csv"))
    sales = pd.read_csv(os.path.join(data_dir, "sales_train_validation.csv"))

    id_cols = ["id", "item_id", "dept_id", "cat_id", "store_id", "state_id"]
    if max_items is not None:
        sampled_items = (
            sales[["item_id", "cat_id"]]
            .drop_duplicates()
            .groupby("cat_id", group_keys=False)
            .apply(lambda g: g.sample(min(len(g), max(1, max_items // sales["cat_id"].nunique())), random_state=random_state))
        )
        sales = sales[sales["item_id"].isin(sampled_items["item_id"])]

    day_cols = [c for c in sales.columns if c.startswith("d_")]
    day_cols = day_cols[-n_last_days:]
    long = sales.melt(id_vars=id_cols, value_vars=day_cols, var_name="d", value_name="sales")
    long = long.merge(cal[["d", "date", "wm_yr_wk", "wday", "snap_CA", "snap_TX", "snap_WI", "event_name_1"]], on="d", how="left")
    # vectorized snap lookup (row-wise .apply(axis=1) is also needlessly slow at scale)
    state_idx = long["state_id"].map({"CA": 0, "TX": 1, "WI": 2}).to_numpy()
    snap_matrix = long[["snap_CA", "snap_TX", "snap_WI"]].to_numpy()
    long["snap"] = snap_matrix[np.arange(len(long)), state_idx]
    long["event_flag"] = long["event_name_1"].notna().astype(int)
    # FIX: merge on wm_yr_wk too, so each day matches exactly ONE week's price row
    # instead of every price row ever recorded for that (store_id, item_id) pair.
    long = long.merge(prices, on=["store_id", "item_id", "wm_yr_wk"], how="left")
    long["sell_price"] = long.groupby(["item_id", "store_id"])["sell_price"].ffill().bfill()
    long["is_weekend"] = long["wday"].isin([1, 2]).astype(int)  # M5 wday convention
    long["revenue"] = long["sales"] * long["sell_price"]
    return long[
        ["id", "item_id", "dept_id", "cat_id", "store_id", "state_id", "date",
         "wday", "sales", "sell_price", "snap", "is_weekend", "event_flag", "revenue"]
    ].sort_values(["id", "date"]).reset_index(drop=True)


if __name__ == "__main__":
    df = build_demand_price_panel()
    print(df.shape)
    print(df.head())
    print(df["cat_id"].value_counts())
