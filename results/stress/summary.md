# Stress benchmark summary

100 cases, 400 solver runs, 1000 repair runs.
All costs come from `roster.evaluator.evaluate`. CP-SAT ran with `num_workers=1` per group inside the parallel stress harness.

## Win rate

A case is won by every solver tied at the lowest evaluator cost; ties share the win equally.

| Solver | Weighted wins | Win rate |
| --- | ---: | ---: |
| greedy | 0.00 | 0.0% |
| local | 52.33 | 52.3% |
| cpsat_cold | 23.83 | 23.8% |
| cpsat_warm | 23.83 | 23.8% |

## Cost gap to the best solver

### By scenario

| Group | Solver | Cases | Mean gap | Median gap |
| --- | --- | ---: | ---: | ---: |
| balanced | greedy | 53 | 11.215 | 8.026 |
| balanced | local | 53 | 0.012 | 0.001 |
| balanced | cpsat_cold | 53 | 15.158 | 2.001 |
| balanced | cpsat_warm | 53 | 5.125 | 0.003 |
| tight | greedy | 27 | 135.596 | 140.024 |
| tight | local | 27 | 0.972 | 0.000 |
| tight | cpsat_cold | 27 | 27.872 | 11.969 |
| tight | cpsat_warm | 27 | 49.244 | 33.973 |
| overloaded | greedy | 20 | 170.661 | 177.009 |
| overloaded | local | 20 | 12.363 | 0.011 |
| overloaded | cpsat_cold | 20 | 9.536 | 2.982 |
| overloaded | cpsat_warm | 20 | 35.539 | 20.468 |

### By days

| Group | Solver | Cases | Mean gap | Median gap |
| --- | --- | ---: | ---: | ---: |
| 7 | greedy | 12 | 11.099 | 6.019 |
| 7 | local | 12 | 0.011 | 0.003 |
| 7 | cpsat_cold | 12 | 1.080 | 0.000 |
| 7 | cpsat_warm | 12 | 0.497 | 0.000 |
| 14 | greedy | 29 | 52.460 | 16.017 |
| 14 | local | 29 | 0.502 | 0.028 |
| 14 | cpsat_cold | 29 | 6.853 | 0.001 |
| 14 | cpsat_warm | 29 | 8.509 | 0.002 |
| 20 | greedy | 37 | 78.816 | 25.989 |
| 20 | local | 37 | 2.928 | 0.000 |
| 20 | cpsat_cold | 37 | 12.929 | 5.994 |
| 20 | cpsat_warm | 37 | 13.663 | 3.979 |
| 28 | greedy | 22 | 140.818 | 187.498 |
| 28 | local | 22 | 6.869 | 0.000 |
| 28 | cpsat_cold | 22 | 48.024 | 56.975 |
| 28 | cpsat_warm | 22 | 70.624 | 39.994 |

## Reliability

| Solver | Runs | hard_pass False | Missed 90 s |
| --- | ---: | ---: | ---: |
| greedy | 100 | 0 (0.0%) | 0 (0.0%) |
| local | 100 | 0 (0.0%) | 0 (0.0%) |
| cpsat_cold | 100 | 0 (0.0%) | 0 (0.0%) |
| cpsat_warm | 100 | 0 (0.0%) | 0 (0.0%) |

## Runtime

| Solver | Median runtime (s) | Mean runtime (s) |
| --- | ---: | ---: |
| greedy | 0.274 | 0.339 |
| local | 55.125 | 50.447 |
| cpsat_cold | 21.614 | 21.165 |
| cpsat_warm | 22.511 | 21.920 |

## Local search vs CP-SAT by load

Compared on 100 cases. 'More than 10% behind' means local cost exceeds the best CP-SAT cost by over 10%.

Local search first falls more than 10% behind at load ratio **0.847** (case_019_balanced_7d_seed1359585649).

This is the lowest load in the sample, so every case is at or above it; 34 of 100 cases are more than 10% behind.

Some of the earliest violations are balanced cases whose total cost is below 0.05 units, where a 10% band is only a few hundredths of a unit. The first material violation (an absolute gap of at least 1.0 cost unit) is at load ratio **1.000** (case_030_tight_14d_seed881875713, local 24.0489 vs CP-SAT 17.0206).

| Case | Load | Local | CP-SAT | Behind |
| --- | ---: | ---: | ---: | ---: |
| case_019_balanced_7d_seed1359585649 | 0.847 | 0.0313 | 0.0125 | 150.3% |
| case_060_balanced_7d_seed1140891794 | 0.848 | 0.0125 | 0.0089 | 40.0% |
| case_036_balanced_20d_seed136577210 | 0.849 | 0.0378 | 0.0093 | 305.7% |
| case_043_balanced_14d_seed494037100 | 0.849 | 0.0257 | 0.0071 | 264.0% |
| case_055_balanced_7d_seed1853836938 | 0.849 | 0.0151 | 0.0126 | 19.3% |
| case_056_balanced_20d_seed1100256834 | 0.849 | 0.0354 | 0.0235 | 50.2% |
| case_068_balanced_20d_seed2008219081 | 0.849 | 0.0349 | 0.0096 | 265.0% |
| case_006_balanced_14d_seed225501379 | 0.850 | 0.0382 | 0.0072 | 430.6% |
| case_009_balanced_20d_seed1295669740 | 0.850 | 0.0398 | 0.0121 | 229.9% |
| case_023_balanced_14d_seed1521098432 | 0.850 | 0.0326 | 0.0085 | 286.1% |
| case_033_balanced_20d_seed438521308 | 0.850 | 0.0356 | 0.0084 | 321.2% |
| case_046_balanced_14d_seed882933208 | 0.850 | 0.0405 | 0.0090 | 351.9% |
| case_057_balanced_20d_seed543056054 | 0.850 | 0.0376 | 0.0110 | 242.8% |
| case_063_balanced_14d_seed2089637963 | 0.850 | 0.0439 | 0.0144 | 204.3% |
| case_064_balanced_14d_seed1040448127 | 0.850 | 0.0358 | 0.0102 | 252.4% |
| case_065_balanced_14d_seed2138587422 | 0.850 | 0.0377 | 0.0120 | 215.0% |
| case_076_balanced_14d_seed1917794518 | 0.850 | 0.0381 | 0.0071 | 433.1% |
| case_087_balanced_14d_seed587204369 | 0.850 | 0.0438 | 0.0100 | 339.2% |
| case_089_balanced_14d_seed1966702770 | 0.850 | 0.0412 | 0.0091 | 350.2% |
| case_011_balanced_20d_seed733803670 | 0.851 | 0.0116 | 0.0102 | 13.4% |
| case_012_balanced_14d_seed1607761709 | 0.851 | 0.0386 | 0.0073 | 426.6% |
| case_025_balanced_14d_seed1274846945 | 0.851 | 0.0389 | 0.0077 | 402.8% |
| case_027_balanced_14d_seed1889002532 | 0.851 | 0.0427 | 0.0128 | 234.2% |
| case_037_balanced_20d_seed592976945 | 0.851 | 0.0370 | 0.0218 | 69.6% |
| case_053_balanced_7d_seed628656808 | 0.851 | 0.0107 | 0.0089 | 20.0% |
| case_067_balanced_14d_seed1028034064 | 0.851 | 0.0446 | 0.0145 | 207.6% |
| case_080_balanced_14d_seed1612979003 | 0.851 | 0.0394 | 0.0117 | 237.6% |
| case_030_tight_14d_seed881875713 | 1.000 | 24.0489 | 17.0206 | 41.3% |
| case_047_tight_14d_seed1217366955 | 1.000 | 38.0471 | 34.0176 | 11.8% |
| case_069_tight_7d_seed1617567425 | 1.000 | 0.0420 | 0.0106 | 296.8% |
| case_085_tight_7d_seed664028155 | 1.000 | 0.0404 | 0.0137 | 195.1% |
| case_096_tight_7d_seed1213488276 | 1.000 | 0.0398 | 0.0105 | 277.9% |
| case_000_tight_20d_seed1133593457 | 1.002 | 56.0367 | 47.0180 | 19.2% |
| case_014_overloaded_20d_seed1561453423 | 1.150 | 375.0426 | 330.0211 | 13.6% |

## Sick-leave repair

| Method | Runs | Mean changes | Median changes | Mean cost | Median cost | Median runtime (s) | Sick slot unfilled | hard_pass False |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| local | 500 | 1.68 | 2.00 | 108.2302 | 2.0107 | 0.024 | 163 (32.6%) | 0 (0.0%) |
| cpsat | 500 | 2.08 | 2.00 | 107.7422 | 0.0456 | 0.190 | 143 (28.6%) | 0 (0.0%) |

## Recommendation

Across all scenarios the lowest mean-cost solver is **local**. Per scenario: **balanced**: local has the lowest mean cost (0.0371 over 53 cases). **tight**: local has the lowest mean cost (88.7498 over 27 cases). **overloaded**: cpsat_cold has the lowest mean cost (426.5189 over 20 cases). Every scenario group has at least 10 cases, so none is thin-evidenced. These results come from the stress harness setting of one CP-SAT search worker per group, which makes CP-SAT timings comparable across cases but weaker than the unrestricted C1 comparison; read the ranking as the behaviour of that constrained configuration, not as an upper bound on CP-SAT quality. For sick-leave repair, local made 1.68 changes on average versus 2.08 for CP-SAT, and left the sick slot unfilled in 32.6% versus 28.6% of events; CP-SAT's mean post-repair cost was 107.7422 versus 108.2302.
