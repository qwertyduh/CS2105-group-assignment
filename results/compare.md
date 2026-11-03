# Solver comparison

Seed **1**, time limit **60 s** per solver per case. Every cost below comes from `roster.evaluator.evaluate`, never from a solver's own objective. CP-SAT gap is the solver's proven bound relative to its own objective; it is reported for information only.

## case_01_balanced_20d

| Solver | Total cost | Unfilled | Hard pass | Pref % | Overwork | Streak | Rest | Spread MPR / CND / Hosp / SPONS | Runtime (s) | CP-SAT gap | Under 90 s |
| --- | ---: | ---: | :---: | ---: | ---: | ---: | ---: | --- | ---: | --- | :---: |
| greedy | 2.0311 | 0 | yes | 74.3 | 2.00 | 0.00 | 0.00 | 8.0 / 16.0 / 20.0 / 8.0 | 0.18 | - | yes |
| local | 0.0366 | 0 | yes | 67.3 | 0.00 | 0.00 | 0.00 | 12.0 / 16.0 / 20.0 / 20.0 | 35.67 | - | yes |
| cpsat_cold | **0.0050** | 0 | yes | 100.0 | 0.00 | 0.00 | 0.00 | 4.0 / 12.0 / 12.0 / 4.0 | 7.64 | 0.0000 (0.00%) | yes |
| cpsat_warm | **0.0050** | 0 | yes | 100.0 | 0.00 | 0.00 | 0.00 | 4.0 / 12.0 / 12.0 / 4.0 | 5.65 | 0.0000 (0.00%) | yes |

## case_02_tight_28d

| Solver | Total cost | Unfilled | Hard pass | Pref % | Overwork | Streak | Rest | Spread MPR / CND / Hosp / SPONS | Runtime (s) | CP-SAT gap | Under 90 s |
| --- | ---: | ---: | :---: | ---: | ---: | ---: | ---: | --- | ---: | --- | :---: |
| greedy | 299.0456 | 59 | yes | 51.6 | 94.00 | 0.00 | 28.00 | 8.0 / 36.0 / 36.0 / 20.0 | 0.21 | - | yes |
| local | 127.0555 | 27 | yes | 33.6 | 46.00 | 0.00 | 0.00 | 8.0 / 36.0 / 32.0 / 16.0 | 60.00 | - | yes |
| cpsat_cold | **62.0145** | 18 | yes | 99.2 | 8.00 | 0.00 | 0.00 | 4.0 / 32.0 / 32.0 / 12.0 | 19.36 | 0.0063 (0.01%) | yes |
| cpsat_warm | 62.0165 | 18 | yes | 96.7 | 8.00 | 0.00 | 0.00 | 4.0 / 32.0 / 32.0 / 12.0 | 19.65 | 0.0082 (0.01%) | yes |

## case_03_overloaded_20d

| Solver | Total cost | Unfilled | Hard pass | Pref % | Overwork | Streak | Rest | Spread MPR / CND / Hosp / SPONS | Runtime (s) | CP-SAT gap | Under 90 s |
| --- | ---: | ---: | :---: | ---: | ---: | ---: | ---: | --- | ---: | --- | :---: |
| greedy | 571.0477 | 145 | yes | 54.7 | 92.00 | 0.00 | 44.00 | 12.0 / 24.0 / 24.0 / 28.0 | 0.20 | - | yes |
| local | 427.0491 | 131 | yes | 52.2 | 34.00 | 0.00 | 0.00 | 12.0 / 24.0 / 28.0 / 24.0 | 32.64 | - | yes |
| cpsat_cold | 351.0227 | 111 | yes | 90.6 | 18.00 | 0.00 | 0.00 | 8.0 / 20.0 / 24.0 / 20.0 | 18.80 | 4.0096 (1.14%) | yes |
| cpsat_warm | **350.0202** | 112 | yes | 96.9 | 14.00 | 0.00 | 0.00 | 8.0 / 24.0 / 24.0 / 20.0 | 19.04 | 3.0071 (0.86%) | yes |

## case_04_small_7d

| Solver | Total cost | Unfilled | Hard pass | Pref % | Overwork | Streak | Rest | Spread MPR / CND / Hosp / SPONS | Runtime (s) | CP-SAT gap | Under 90 s |
| --- | ---: | ---: | :---: | ---: | ---: | ---: | ---: | --- | ---: | --- | :---: |
| greedy | 0.0287 | 0 | yes | 80.8 | 0.00 | 0.00 | 0.00 | 4.0 / 8.0 / 8.0 / 4.0 | 0.02 | - | yes |
| local | 0.0113 | 0 | yes | 99.2 | 0.00 | 0.00 | 0.00 | 4.0 / 8.0 / 4.0 / 8.0 | 35.72 | - | yes |
| cpsat_cold | **0.0089** | 0 | yes | 100.0 | 0.00 | 0.00 | 0.00 | 4.0 / 8.0 / 4.0 / 4.0 | 0.91 | 0.0000 (0.00%) | yes |
| cpsat_warm | **0.0089** | 0 | yes | 100.0 | 0.00 | 0.00 | 0.00 | 4.0 / 8.0 / 4.0 / 4.0 | 0.92 | 0.0000 (0.00%) | yes |

## case_tight_28d_seed7

| Solver | Total cost | Unfilled | Hard pass | Pref % | Overwork | Streak | Rest | Spread MPR / CND / Hosp / SPONS | Runtime (s) | CP-SAT gap | Under 90 s |
| --- | ---: | ---: | :---: | ---: | ---: | ---: | ---: | --- | ---: | --- | :---: |
| greedy | 338.0462 | 66 | yes | 54.7 | 116.00 | 0.00 | 24.00 | 36.0 / 8.0 / 36.0 / 32.0 | 0.34 | - | yes |
| local | 156.0488 | 38 | yes | 48.3 | 42.00 | 0.00 | 0.00 | 32.0 / 4.0 / 36.0 / 36.0 | 39.82 | - | yes |
| cpsat_cold | 103.0198 | 31 | yes | 93.0 | 8.00 | 0.00 | 2.00 | 32.0 / 8.0 / 36.0 / 32.0 | 19.56 | 20.0095 (19.42%) | yes |
| cpsat_warm | **99.0222** | 29 | yes | 88.1 | 12.00 | 0.00 | 0.00 | 32.0 / 4.0 / 36.0 / 32.0 | 19.99 | 16.0123 (16.17%) | yes |

## Read-out

case_01_balanced_20d: cpsat_cold and cpsat_warm tied at 0.0050, 86.4% below local (0.0366); case_02_tight_28d: cpsat_cold won at 62.0145, 0.0% below cpsat_warm (62.0165); case_03_overloaded_20d: cpsat_warm won at 350.0202, 0.3% below cpsat_cold (351.0227); case_04_small_7d: cpsat_cold and cpsat_warm tied at 0.0089, 20.8% below local (0.0113); case_tight_28d_seed7: cpsat_warm won at 99.0222, 3.9% below cpsat_cold (103.0198). Local search did not come within 5% of CP-SAT on any case.

## Sanity checks

- WARNING: case_02_tight_28d: cpsat_warm (62.0165) > cpsat_cold (62.0145); costs: greedy=299.0456, local=127.0555, cpsat_cold=62.0145, cpsat_warm=62.0165
  - Investigation: The two CP-SAT runs differ by only 0.0020 units (0.003%). Both have exactly the same hard-rule outcome and rule-breach components (unfilled=54, overwork=8, streak=0, rest=0); the difference is entirely in the soft preference term (warm 96.7% satisfied vs cold 99.2%). Both groups finished FEASIBLE at the 60 s limit (warm gap 0.0082, cold gap 0.0063), so neither run proved optimality and their costs differ by less than either proven gap. The greedy hint can steer the multi-threaded search onto a slightly worse near-optimal incumbent on a tight case; it is not a correctness or hard-rule problem, and on the other four cases warm is better than or tied with cold.
