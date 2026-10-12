# Synthesizer sample reports

Full pipeline (`DriftDetectionPipeline`: Sentinel -> Investigator -> rule-based Synthesizer), unchanged, seed 42, no calendar context. One report per alerted feature. The true slice is shown for the reader only; the pipeline never sees it.


## m5 / sudden / trial 0 (2015-12-26..2016-01-25)

**True slice:** `store_id=CA_2`


### Report: `sales` (rule_based)

- Top slices: `store_id=CA_2|dept_id=HOBBIES_2|dept_id=FOODS_3` (top-1 = true slice)

Drift detected in 'sales' (JS=0.1253, L-inf=0.2640). The strongest localized explanation is store_id='CA_2' (divergence-removal score=0.0186, covering 10.0% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for store_id='CA_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: dept_id='HOBBIES_2' (score=0.0074).


### Report: `sell_price` (rule_based)

- Top slices: `dept_id=FOODS_3|cat_id=FOODS|dept_id=FOODS_2`

Drift detected in 'sell_price' (JS=0.0333, L-inf=0.0650). The strongest localized explanation is dept_id='FOODS_3' (divergence-removal score=0.0155, covering 20.2% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_3' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sell_price.
- Secondary candidate to rule out: cat_id='FOODS' (score=0.0081).


### Report: `revenue` (rule_based)

- Top slices: `dept_id=HOBBIES_2|dept_id=FOODS_3|dept_id=FOODS_2`

Drift detected in 'revenue' (JS=0.1523, L-inf=0.2640). The strongest localized explanation is dept_id='HOBBIES_2' (divergence-removal score=0.0059, covering 8.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='HOBBIES_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: dept_id='FOODS_3' (score=0.0033).


## m5 / sudden / trial 1 (2015-12-24..2016-01-23)

**True slice:** `state_id=CA`


### Report: `sales` (rule_based)

- Top slices: `store_id=CA_2|state_id=CA|store_id=CA_1`

Drift detected in 'sales' (JS=0.0315, L-inf=0.0576). The strongest localized explanation is store_id='CA_2' (divergence-removal score=0.1145, covering 10.0% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for store_id='CA_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: state_id='CA' (score=0.1051).


### Report: `sell_price` (rule_based)

- Top slices: `dept_id=FOODS_3|cat_id=FOODS|dept_id=FOODS_2`

Drift detected in 'sell_price' (JS=0.0350, L-inf=0.0683). The strongest localized explanation is dept_id='FOODS_3' (divergence-removal score=0.0142, covering 20.2% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_3' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sell_price.
- Secondary candidate to rule out: cat_id='FOODS' (score=0.0074).


### Report: `revenue` (rule_based)

- Top slices: `dept_id=HOBBIES_2|dept_id=FOODS_1|dept_id=FOODS_3`

Drift detected in 'revenue' (JS=0.1329, L-inf=0.2317). The strongest localized explanation is dept_id='HOBBIES_2' (divergence-removal score=0.0074, covering 8.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='HOBBIES_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: dept_id='FOODS_1' (score=0.0016).


## m5 / sudden / trial 2 (2016-01-21..2016-02-20)

**True slice:** `state_id=WI`


### Report: `sales` (rule_based)

- Top slices: `store_id=WI_1|state_id=WI|store_id=WI_2`

Drift detected in 'sales' (JS=0.0192, L-inf=0.0336). The strongest localized explanation is store_id='WI_1' (divergence-removal score=0.1031, covering 10.0% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for store_id='WI_1' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: state_id='WI' (score=0.0901).


### Report: `sell_price` (rule_based)

- Top slices: `dept_id=FOODS_3|dept_id=FOODS_1|cat_id=FOODS`

Drift detected in 'sell_price' (JS=0.0412, L-inf=0.0800). The strongest localized explanation is dept_id='FOODS_3' (divergence-removal score=0.0051, covering 20.2% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_3' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sell_price.
- Secondary candidate to rule out: dept_id='FOODS_1' (score=0.0037).


### Report: `revenue` (rule_based)

- Top slices: `dept_id=FOODS_3|store_id=WI_2|cat_id=FOODS`

Drift detected in 'revenue' (JS=0.0037, L-inf=0.0706). The strongest localized explanation is dept_id='FOODS_3' (divergence-removal score=0.0053, covering 20.2% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_3' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: store_id='WI_2' (score=0.0037).


## m5 / gradual / trial 0 (2016-02-14..2016-03-15)

**True slice:** `state_id=TX`


### Report: `sales` (rule_based)

- Top slices: `dept_id=FOODS_2|store_id=TX_1|dept_id=FOODS_3`

Drift detected in 'sales' (JS=0.0144, L-inf=0.0610). The strongest localized explanation is dept_id='FOODS_2' (divergence-removal score=0.0051, covering 7.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: store_id='TX_1' (score=0.0039).


### Report: `sell_price` (rule_based)

- Top slices: `dept_id=FOODS_2|dept_id=FOODS_3|dept_id=HOBBIES_1`

Drift detected in 'sell_price' (JS=0.0203, L-inf=0.0400). The strongest localized explanation is dept_id='FOODS_2' (divergence-removal score=0.0166, covering 7.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sell_price.
- Secondary candidate to rule out: dept_id='FOODS_3' (score=0.0014).


### Report: `revenue` (rule_based)

- Top slices: `dept_id=FOODS_2|dept_id=FOODS_3|cat_id=FOODS`

Drift detected in 'revenue' (JS=0.0228, L-inf=0.1598). The strongest localized explanation is dept_id='FOODS_2' (divergence-removal score=0.0046, covering 7.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: dept_id='FOODS_3' (score=0.0032).


## m5 / gradual / trial 1 (2015-10-19..2015-11-18)

**True slice:** `item_id=HOUSEHOLD_1_458`


### Report: `sales` (rule_based)

- Top slices: `dept_id=HOBBIES_2|dept_id=FOODS_1|store_id=CA_2`

Drift detected in 'sales' (JS=0.0260, L-inf=0.1511). The strongest localized explanation is dept_id='HOBBIES_2' (divergence-removal score=0.0031, covering 8.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='HOBBIES_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: dept_id='FOODS_1' (score=0.0015).


### Report: `sell_price` (rule_based)

- Top slices: `dept_id=FOODS_1|dept_id=FOODS_2|store_id=CA_3`

Drift detected in 'sell_price' (JS=0.0514, L-inf=0.0967). The strongest localized explanation is dept_id='FOODS_1' (divergence-removal score=0.0069, covering 5.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_1' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sell_price.
- Secondary candidate to rule out: dept_id='FOODS_2' (score=0.0035).


### Report: `revenue` (rule_based)

- Top slices: `dept_id=HOBBIES_2|dept_id=FOODS_1|store_id=WI_3`

Drift detected in 'revenue' (JS=0.0388, L-inf=0.0859). The strongest localized explanation is dept_id='HOBBIES_2' (divergence-removal score=0.0029, covering 8.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='HOBBIES_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: dept_id='FOODS_1' (score=0.0022).


## m5 / gradual / trial 2 (2016-02-05..2016-03-06)

**True slice:** `cat_id=HOBBIES`


### Report: `sales` (rule_based)

- Top slices: `dept_id=FOODS_3|dept_id=FOODS_2|store_id=WI_2`

Drift detected in 'sales' (JS=0.0035, L-inf=0.0666). The strongest localized explanation is dept_id='FOODS_3' (divergence-removal score=0.0047, covering 20.2% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_3' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: dept_id='FOODS_2' (score=0.0036).


### Report: `sell_price` (rule_based)

- Top slices: `dept_id=FOODS_2|dept_id=FOODS_1|dept_id=FOODS_3`

Drift detected in 'sell_price' (JS=0.0281, L-inf=0.0550). The strongest localized explanation is dept_id='FOODS_2' (divergence-removal score=0.0075, covering 7.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sell_price.
- Secondary candidate to rule out: dept_id='FOODS_1' (score=0.0026).


### Report: `revenue` (rule_based)

- Top slices: `dept_id=FOODS_3|dept_id=FOODS_2|store_id=WI_2`

Drift detected in 'revenue' (JS=0.0035, L-inf=0.0664). The strongest localized explanation is dept_id='FOODS_3' (divergence-removal score=0.0047, covering 20.2% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_3' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: dept_id='FOODS_2' (score=0.0040).


## m5 / intermittent / trial 0 (2015-10-07..2015-11-06)

**True slice:** `item_id=HOUSEHOLD_2_450`


### Report: `sales` (rule_based)

- Top slices: `dept_id=HOBBIES_2|dept_id=FOODS_1|dept_id=FOODS_2`

Drift detected in 'sales' (JS=0.0165, L-inf=0.0880). The strongest localized explanation is dept_id='HOBBIES_2' (divergence-removal score=0.0072, covering 8.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='HOBBIES_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: dept_id='FOODS_1' (score=0.0026).


### Report: `sell_price` (rule_based)

- Top slices: `dept_id=FOODS_2|dept_id=FOODS_1|store_id=CA_3`

Drift detected in 'sell_price' (JS=0.0147, L-inf=0.0677). The strongest localized explanation is dept_id='FOODS_2' (divergence-removal score=0.0023, covering 7.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sell_price.
- Secondary candidate to rule out: dept_id='FOODS_1' (score=0.0016).


### Report: `revenue` (rule_based)

- Top slices: `dept_id=HOBBIES_2|dept_id=FOODS_2|dept_id=FOODS_1`

Drift detected in 'revenue' (JS=0.0058, L-inf=0.0880). The strongest localized explanation is dept_id='HOBBIES_2' (divergence-removal score=0.0062, covering 8.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='HOBBIES_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: dept_id='FOODS_2' (score=0.0029).


## m5 / intermittent / trial 1 (2016-03-14..2016-04-13)

**True slice:** `item_id=HOUSEHOLD_1_313`


### Report: `sales` (rule_based)

- Top slices: `dept_id=FOODS_2|store_id=CA_4|store_id=WI_2`

Drift detected in 'sales' (JS=0.0194, L-inf=0.0431). The strongest localized explanation is dept_id='FOODS_2' (divergence-removal score=0.0037, covering 7.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: store_id='CA_4' (score=0.0025).


### Report: `sell_price` (rule_based)

- Top slices: `dept_id=FOODS_3|cat_id=FOODS|dept_id=HOUSEHOLD_1`

Drift detected in 'sell_price' (JS=0.0501, L-inf=0.0967). The strongest localized explanation is dept_id='FOODS_3' (divergence-removal score=0.0045, covering 20.2% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_3' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sell_price.
- Secondary candidate to rule out: cat_id='FOODS' (score=0.0020).


### Report: `revenue` (rule_based)

- Top slices: `dept_id=FOODS_2|store_id=WI_2|dept_id=FOODS_1`

Drift detected in 'revenue' (JS=0.0085, L-inf=0.0870). The strongest localized explanation is dept_id='FOODS_2' (divergence-removal score=0.0022, covering 7.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: store_id='WI_2' (score=0.0013).


## m5 / intermittent / trial 2 (2015-10-30..2015-11-29)

**True slice:** `item_id=FOODS_3_093`


### Report: `sales` (rule_based)

- Top slices: `dept_id=FOODS_2|dept_id=FOODS_1|store_id=TX_3`

Drift detected in 'sales' (JS=0.0205, L-inf=0.0503). The strongest localized explanation is dept_id='FOODS_2' (divergence-removal score=0.0024, covering 7.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: dept_id='FOODS_1' (score=0.0015).


### Report: `sell_price` (rule_based)

- Top slices: `dept_id=FOODS_1|dept_id=FOODS_2|store_id=CA_3`

Drift detected in 'sell_price' (JS=0.0411, L-inf=0.0783). The strongest localized explanation is dept_id='FOODS_1' (divergence-removal score=0.0050, covering 5.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_1' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sell_price.
- Secondary candidate to rule out: dept_id='FOODS_2' (score=0.0028).


### Report: `revenue` (rule_based)

- Top slices: `dept_id=FOODS_2|dept_id=HOBBIES_2|dept_id=FOODS_1`

Drift detected in 'revenue' (JS=0.0160, L-inf=0.0612). The strongest localized explanation is dept_id='FOODS_2' (divergence-removal score=0.0022, covering 7.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: dept_id='HOBBIES_2' (score=0.0012).


## m5 / drift_free / trial 0 (2015-10-07..2015-11-06)

**True slice:** `(none: drift-free)`


### Report: `sales` (rule_based)

- Top slices: `dept_id=HOBBIES_2|dept_id=FOODS_1|dept_id=FOODS_2`

Drift detected in 'sales' (JS=0.0165, L-inf=0.0880). The strongest localized explanation is dept_id='HOBBIES_2' (divergence-removal score=0.0072, covering 8.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='HOBBIES_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: dept_id='FOODS_1' (score=0.0026).


### Report: `sell_price` (rule_based)

- Top slices: `dept_id=FOODS_2|dept_id=FOODS_1|store_id=CA_3`

Drift detected in 'sell_price' (JS=0.0147, L-inf=0.0677). The strongest localized explanation is dept_id='FOODS_2' (divergence-removal score=0.0023, covering 7.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sell_price.
- Secondary candidate to rule out: dept_id='FOODS_1' (score=0.0016).


### Report: `revenue` (rule_based)

- Top slices: `dept_id=HOBBIES_2|dept_id=FOODS_2|dept_id=FOODS_1`

Drift detected in 'revenue' (JS=0.0058, L-inf=0.0880). The strongest localized explanation is dept_id='HOBBIES_2' (divergence-removal score=0.0062, covering 8.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='HOBBIES_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: dept_id='FOODS_2' (score=0.0029).


## m5 / drift_free / trial 1 (2016-03-25..2016-04-24)

**True slice:** `(none: drift-free)`


### Report: `sales` (rule_based)

- Top slices: `dept_id=FOODS_2|dept_id=FOODS_1|store_id=CA_2`

Drift detected in 'sales' (JS=0.0171, L-inf=0.0341). The strongest localized explanation is dept_id='FOODS_2' (divergence-removal score=0.0029, covering 7.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_2' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: dept_id='FOODS_1' (score=0.0017).


### Report: `sell_price` (rule_based)

- Top slices: `dept_id=HOUSEHOLD_1|dept_id=FOODS_3|cat_id=HOUSEHOLD`

Drift detected in 'sell_price' (JS=0.0403, L-inf=0.0783). The strongest localized explanation is dept_id='HOUSEHOLD_1' (divergence-removal score=0.0039, covering 16.2% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='HOUSEHOLD_1' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sell_price.
- Secondary candidate to rule out: dept_id='FOODS_3' (score=0.0031).


### Report: `revenue` (rule_based)

- Top slices: `dept_id=FOODS_1|dept_id=FOODS_2|dept_id=HOUSEHOLD_2`

Drift detected in 'revenue' (JS=0.0107, L-inf=0.0948). The strongest localized explanation is dept_id='FOODS_1' (divergence-removal score=0.0015, covering 5.6% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FOODS_1' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: dept_id='FOODS_2' (score=0.0012).


## favorita / sudden / trial 0 (2017-04-17..2017-05-17)

**True slice:** `store_id=17`


### Report: `sales` (rule_based)

- Top slices: `store_id=52|dept_id=PREPARED FOODS|state_id=Manabi`

Drift detected in 'sales' (JS=0.0410, L-inf=0.1382). The strongest localized explanation is store_id='52' (divergence-removal score=0.3558, covering 2.1% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for store_id='52' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: dept_id='PREPARED FOODS' (score=0.0871).


### Report: `revenue` (rule_based)

- Top slices: `store_id=52|dept_id=PREPARED FOODS|state_id=Manabi`

Drift detected in 'revenue' (JS=0.0410, L-inf=0.1382). The strongest localized explanation is store_id='52' (divergence-removal score=0.3558, covering 2.1% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for store_id='52' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: dept_id='PREPARED FOODS' (score=0.0872).


## favorita / sudden / trial 1 (2017-04-15..2017-05-15)

**True slice:** `state_id=Chimborazo`


### Report: `sales` (rule_based)

- Top slices: `store_id=52|dept_id=PREPARED FOODS|state_id=Manabi`

Drift detected in 'sales' (JS=0.0384, L-inf=0.1347). The strongest localized explanation is store_id='52' (divergence-removal score=0.3256, covering 2.1% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for store_id='52' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: dept_id='PREPARED FOODS' (score=0.0935).


### Report: `revenue` (rule_based)

- Top slices: `store_id=52|dept_id=PREPARED FOODS|state_id=Manabi`

Drift detected in 'revenue' (JS=0.0384, L-inf=0.1347). The strongest localized explanation is store_id='52' (divergence-removal score=0.3256, covering 2.1% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for store_id='52' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: dept_id='PREPARED FOODS' (score=0.0935).


## favorita / sudden / trial 2 (2017-05-13..2017-06-12)

**True slice:** `state_id=Manabi`


### Report: `sales` (rule_based)

- Top slices: `store_id=52|state_id=Manabi|dept_id=BOOKS`

Drift detected in 'sales' (JS=0.1016, L-inf=0.3414). The strongest localized explanation is store_id='52' (divergence-removal score=0.1013, covering 2.1% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for store_id='52' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: state_id='Manabi' (score=0.0375).


### Report: `revenue` (rule_based)

- Top slices: `store_id=52|dept_id=BOOKS|dept_id=PREPARED FOODS`

Drift detected in 'revenue' (JS=0.1017, L-inf=0.3414). The strongest localized explanation is store_id='52' (divergence-removal score=0.0838, covering 2.1% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for store_id='52' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: dept_id='BOOKS' (score=0.0345).


## favorita / gradual / trial 0 (2017-06-06..2017-07-06)

**True slice:** `state_id=Guayas`


### Report: `sales` (rule_based)

- Top slices: `dept_id=SCHOOL AND OFFICE SUPPLIES|store_id=29|store_id=24`

Drift detected in 'sales' (JS=0.2103, L-inf=0.4105). The strongest localized explanation is dept_id='SCHOOL AND OFFICE SUPPLIES' (divergence-removal score=0.0378, covering 3.0% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='SCHOOL AND OFFICE SUPPLIES' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: store_id='29' (score=0.0268).


### Report: `revenue` (rule_based)

- Top slices: `dept_id=SCHOOL AND OFFICE SUPPLIES|dept_id=BOOKS|dept_id=DAIRY`

Drift detected in 'revenue' (JS=0.2099, L-inf=0.4105). The strongest localized explanation is dept_id='SCHOOL AND OFFICE SUPPLIES' (divergence-removal score=0.0387, covering 3.0% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='SCHOOL AND OFFICE SUPPLIES' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: dept_id='BOOKS' (score=0.0223).


## favorita / gradual / trial 1 (2017-02-08..2017-03-10)

**True slice:** `item_id=511394`


### Report: `sales` (rule_based)

- Top slices: `dept_id=FROZEN FOODS|dept_id=BOOKS|dept_id=CLEANING`

Drift detected in 'sales' (JS=0.0177, L-inf=0.0945). The strongest localized explanation is dept_id='FROZEN FOODS' (divergence-removal score=0.0534, covering 3.8% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FROZEN FOODS' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: dept_id='BOOKS' (score=0.0220).


### Report: `revenue` (rule_based)

- Top slices: `dept_id=FROZEN FOODS|dept_id=BOOKS|dept_id=CLEANING`

Drift detected in 'revenue' (JS=0.0177, L-inf=0.0945). The strongest localized explanation is dept_id='FROZEN FOODS' (divergence-removal score=0.0534, covering 3.8% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FROZEN FOODS' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: dept_id='BOOKS' (score=0.0220).


## favorita / gradual / trial 2 (2017-05-28..2017-06-27)

**True slice:** `cat_id=PERISHABLE`


### Report: `sales` (rule_based)

- Top slices: `dept_id=DAIRY|dept_id=SCHOOL AND OFFICE SUPPLIES|dept_id=BOOKS`

Drift detected in 'sales' (JS=0.2210, L-inf=0.4553). The strongest localized explanation is dept_id='DAIRY' (divergence-removal score=0.0416, covering 3.8% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='DAIRY' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: dept_id='SCHOOL AND OFFICE SUPPLIES' (score=0.0361).


### Report: `revenue` (rule_based)

- Top slices: `dept_id=SCHOOL AND OFFICE SUPPLIES|dept_id=BOOKS|dept_id=DAIRY`

Drift detected in 'revenue' (JS=0.2205, L-inf=0.4553). The strongest localized explanation is dept_id='SCHOOL AND OFFICE SUPPLIES' (divergence-removal score=0.0358, covering 3.0% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='SCHOOL AND OFFICE SUPPLIES' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: dept_id='BOOKS' (score=0.0342).


## favorita / intermittent / trial 0 (2017-01-27..2017-02-26)

**True slice:** `item_id=850388`


### Report: `sales` (rule_based)

- Top slices: `dept_id=FROZEN FOODS|dept_id=HOME APPLIANCES|dept_id=BOOKS`

Drift detected in 'sales' (JS=0.0333, L-inf=0.1180). The strongest localized explanation is dept_id='FROZEN FOODS' (divergence-removal score=0.0643, covering 3.8% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FROZEN FOODS' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: dept_id='HOME APPLIANCES' (score=0.0206).


### Report: `revenue` (rule_based)

- Top slices: `dept_id=FROZEN FOODS|dept_id=HOME APPLIANCES|dept_id=BOOKS`

Drift detected in 'revenue' (JS=0.0333, L-inf=0.1180). The strongest localized explanation is dept_id='FROZEN FOODS' (divergence-removal score=0.0618, covering 3.8% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FROZEN FOODS' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: dept_id='HOME APPLIANCES' (score=0.0206).


## favorita / intermittent / trial 1 (2017-07-05..2017-08-04)

**True slice:** `item_id=315463`


### Report: `sales` (rule_based)

- Top slices: `dept_id=HOME APPLIANCES|dept_id=PRODUCE|dept_id=BOOKS`

Drift detected in 'sales' (JS=0.0840, L-inf=0.1749). The strongest localized explanation is dept_id='HOME APPLIANCES' (divergence-removal score=0.0238, covering 0.7% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='HOME APPLIANCES' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: dept_id='PRODUCE' (score=0.0099).


### Report: `revenue` (rule_based)

- Top slices: `dept_id=HOME APPLIANCES|dept_id=PRODUCE|dept_id=BOOKS`

Drift detected in 'revenue' (JS=0.0840, L-inf=0.1749). The strongest localized explanation is dept_id='HOME APPLIANCES' (divergence-removal score=0.0238, covering 0.7% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='HOME APPLIANCES' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: dept_id='PRODUCE' (score=0.0099).


## favorita / intermittent / trial 2 (2017-02-19..2017-03-21)

**True slice:** `item_id=1386120`


### Report: `sales` (rule_based)

- Top slices: `dept_id=BOOKS|dept_id=FROZEN FOODS|dept_id=HOME APPLIANCES`

Drift detected in 'sales' (JS=0.0239, L-inf=0.0651). The strongest localized explanation is dept_id='BOOKS' (divergence-removal score=0.0208, covering 0.4% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='BOOKS' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: dept_id='FROZEN FOODS' (score=0.0198).


### Report: `revenue` (rule_based)

- Top slices: `dept_id=BOOKS|dept_id=FROZEN FOODS|dept_id=HOME APPLIANCES`

Drift detected in 'revenue' (JS=0.0239, L-inf=0.0651). The strongest localized explanation is dept_id='BOOKS' (divergence-removal score=0.0202, covering 0.4% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='BOOKS' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: dept_id='FROZEN FOODS' (score=0.0198).


## favorita / drift_free / trial 0 (2017-01-27..2017-02-26)

**True slice:** `(none: drift-free)`


### Report: `sales` (rule_based)

- Top slices: `dept_id=FROZEN FOODS|dept_id=HOME APPLIANCES|dept_id=BOOKS`

Drift detected in 'sales' (JS=0.0333, L-inf=0.1180). The strongest localized explanation is dept_id='FROZEN FOODS' (divergence-removal score=0.0618, covering 3.8% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FROZEN FOODS' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: dept_id='HOME APPLIANCES' (score=0.0206).


### Report: `revenue` (rule_based)

- Top slices: `dept_id=FROZEN FOODS|dept_id=HOME APPLIANCES|dept_id=BOOKS`

Drift detected in 'revenue' (JS=0.0333, L-inf=0.1180). The strongest localized explanation is dept_id='FROZEN FOODS' (divergence-removal score=0.0618, covering 3.8% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='FROZEN FOODS' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: dept_id='HOME APPLIANCES' (score=0.0206).


## favorita / drift_free / trial 1 (2017-07-16..2017-08-15)

**True slice:** `(none: drift-free)`


### Report: `sales` (rule_based)

- Top slices: `dept_id=HOME APPLIANCES|dept_id=SCHOOL AND OFFICE SUPPLIES|dept_id=BREAD/BAKERY`

Drift detected in 'sales' (JS=0.0249, L-inf=0.0623). The strongest localized explanation is dept_id='HOME APPLIANCES' (divergence-removal score=0.0399, covering 0.7% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='HOME APPLIANCES' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on sales.
- Secondary candidate to rule out: dept_id='SCHOOL AND OFFICE SUPPLIES' (score=0.0239).


### Report: `revenue` (rule_based)

- Top slices: `dept_id=HOME APPLIANCES|dept_id=SCHOOL AND OFFICE SUPPLIES|dept_id=BREAD/BAKERY`

Drift detected in 'revenue' (JS=0.0249, L-inf=0.0623). The strongest localized explanation is dept_id='HOME APPLIANCES' (divergence-removal score=0.0399, covering 0.7% of the current window).

**Recommended actions:**
- Check pipeline/ingestion logs for dept_id='HOME APPLIANCES' around this window.
- Compare against the SHAP-based localization baseline for a second opinion on revenue.
- Secondary candidate to rule out: dept_id='SCHOOL AND OFFICE SUPPLIES' (score=0.0239).
