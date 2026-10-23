# Warm-up/cool-down statistical analysis

The notebooks in this directory generate the tables and figures needed for the
research questions in [`docs.md`](../../docs.md).

## Pipeline

1. Load `experiments/warmup_cooldown/run_table.csv`.
2. Keep completed (`__done == "DONE"`) observations and coerce the measured
   columns to numeric values. Record all exclusions and cell counts.
3. Describe energy, benchmark execution time, total experiment time, and
   absolute deviation from each benchmark-duration median.
4. For RQ0, inspect residual normality and cell variance. Because the
   assumptions are violated, use an aligned rank transform (ART) factorial
   analysis for duration, benchmark, and their interaction. Follow this with
   duration-by-benchmark comparisons against the 30 s baseline. The pairwise
   p-values use Benjamini-Hochberg correction and include relative change and
   an effect size.
5. For RQ1, use the report's variability measure
   `A = abs(energy - cell_median)` for inference, while reporting standard
   deviation descriptively. Compare each duration with the 210 s reference
   using one-sided Mann-Whitney tests, adjust p-values with Benjamini-Hochberg,
   and combine the result with total time and the report's 10% rule.
6. Write all tables as CSV files beside the notebooks and figures as PNG
   files, so report tables can be copied without relying on notebook display
   state.

The ART factorial results are written to `table_art_anova.csv`.

Install the analysis dependencies with:

```bash
python3 -m pip install -r warmup_cooldown/statistics/requirements.txt
```

Run `01_descriptive_tables.ipynb` first and then
`02_hypothesis_tests_rq0_rq1.ipynb` from the repository root. The second
notebook consumes `cleaned_runs.csv` produced by the first. The notebooks
resolve paths by searching upward from the current directory, so they also
work when opened in VS Code.
