# Stress benchmark summary

6 cases, 24 solver runs, 60 repair runs.
All costs come from `roster.evaluator.evaluate`. CP-SAT ran with `num_workers=1` per group inside the parallel stress harness.

## Win rate

A case is won by every solver tied at the lowest evaluator cost; ties share the win equally.

| Solver | Weighted wins | Win rate |
| --- | ---: | ---: |
| greedy | 0.00 | 0.0% |
| local | 6.00 | 100.0% |
| cpsat_cold | 0.00 | 0.0% |
| cpsat_warm | 0.00 | 0.0% |

## Cost gap to the best solver

### By scenario

| Group | Solver | Cases | Mean gap | Median gap |
| --- | --- | ---: | ---: | ---: |
| balanced | greedy | 2 | 3.989 | 3.989 |
| balanced | local | 2 | 0.000 | 0.000 |
| balanced | cpsat_cold | 2 | 73.468 | 73.468 |
| balanced | cpsat_warm | 2 | 3.989 | 3.989 |
| tight | greedy | 3 | 110.992 | 119.989 |
| tight | local | 3 | 0.000 | 0.000 |
| tight | cpsat_cold | 3 | 1712.966 | 71.963 |
| tight | cpsat_warm | 3 | 591.315 | 97.982 |
| overloaded | greedy | 1 | 115.996 | 115.996 |
| overloaded | local | 1 | 0.000 | 0.000 |
| overloaded | cpsat_cold | 1 | 2300.966 | 2300.966 |
| overloaded | cpsat_warm | 1 | 94.990 | 94.990 |

### By days

| Group | Solver | Cases | Mean gap | Median gap |
| --- | --- | ---: | ---: | ---: |
| 20 | greedy | 4 | 54.239 | 46.491 |
| 20 | local | 4 | 0.000 | 0.000 |
| 20 | cpsat_cold | 4 | 67.466 | 61.465 |
| 20 | cpsat_warm | 4 | 47.987 | 44.990 |
| 28 | greedy | 2 | 119.996 | 119.996 |
| 28 | local | 2 | 0.000 | 0.000 |
| 28 | cpsat_cold | 2 | 3658.467 | 3658.467 |
| 28 | cpsat_warm | 2 | 842.482 | 842.482 |

## Reliability

| Solver | Runs | hard_pass False | Missed 90 s |
| --- | ---: | ---: | ---: |
| greedy | 6 | 0 (0.0%) | 0 (0.0%) |
| local | 6 | 0 (0.0%) | 0 (0.0%) |
| cpsat_cold | 6 | 0 (0.0%) | 0 (0.0%) |
| cpsat_warm | 6 | 0 (0.0%) | 0 (0.0%) |

## Runtime

| Solver | Median runtime (s) | Mean runtime (s) |
| --- | ---: | ---: |
| greedy | 0.313 | 0.353 |
| local | 5.001 | 4.766 |
| cpsat_cold | 3.339 | 3.436 |
| cpsat_warm | 3.829 | 3.935 |

## Local search vs CP-SAT by load

Compared on 6 cases. 'More than 10% behind' means local cost exceeds the best CP-SAT cost by over 10%.

No case had local search more than 10% behind CP-SAT, so no load threshold is identified.

## Sick-leave repair

| Method | Runs | Mean changes | Median changes | Mean cost | Median cost | Median runtime (s) | Sick slot unfilled | hard_pass False |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| local | 30 | 1.40 | 1.00 | 200.1148 | 113.5513 | 0.022 | 18 (60.0%) | 0 (0.0%) |
| cpsat | 30 | 1.93 | 2.00 | 199.5148 | 113.5513 | 0.221 | 16 (53.3%) | 0 (0.0%) |

## Recommendation

Across all scenarios the lowest mean-cost solver is **local**. Per scenario: **balanced**: local has the lowest mean cost (0.0442 over 2 cases) - evidence is thin (fewer than 10 cases in this group). **tight**: local has the lowest mean cost (182.3836 over 3 cases) - evidence is thin (fewer than 10 cases in this group). **overloaded**: local has the lowest mean cost (643.0499 over 1 cases) - evidence is thin (fewer than 10 cases in this group). Treat any scenario with fewer than 10 cases as indicative only; the benchmark's confidence comes from the aggregate trend, not from a single case.
