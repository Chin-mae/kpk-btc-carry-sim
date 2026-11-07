# Generator Calibration and Validation Report (SPEC §7)

This document records the calibration and statistical validation results for the price generators specified in SPEC §7, evaluated against 33,588 historical hourly BTC/USD candles (33,587 log returns) spanning 1 January 2023 through 31 October 2026.

## 1. Verbatim Calibration Output

The following block captures the verbatim console output from `notebooks/01_calibration.py` (run with calibration seed 42 across 100 paths of 720 hours each, totaling 72,000 simulated hourly steps):

```
================================================================================
 KPK CARRY SIM - GENERATOR CALIBRATION & VALIDATION SUITE (SPEC §7)
================================================================================
Calibration seed: 42
Simulation scale: 100 paths x 720 hours = 72,000 simulated steps

Realised historical data: 33,587 hourly observations (Jan 2023 - Oct 2026)
  Mean hourly return:  +4.603429e-05
  Hourly volatility:   0.004918 (46.03% ann.)
  Skewness:            -0.1332
  Excess Kurtosis:     12.8472
  Worst 1% move:       -1.4864%
  Autocorr(r^2) lag 1: 0.1775

--------------------------------------------------------------------------------
 MOMENTS & TAIL COMPARISON TABLE (Realised vs Simulated)
--------------------------------------------------------------------------------
Metric                    | Realised     | Bootstrap    | Jump Diff    | Regime Switch
--------------------------------------------------------------------------------------
Mean Hourly Return        | +4.603429e-05 | +6.495240e-05 | -1.132882e-05 | -1.828028e-05
Hourly Volatility (std)   | 0.004918     | 0.004887     | 0.004913     | 0.004888    
Annualised Volatility     | 46.03%       | 45.74%       | 45.99%       | 45.75%      
Skewness                  | -0.1332      | -0.0239      | -0.9494      | -0.3930     
Excess Kurtosis           | 12.85        | 12.23        | 6.74         | 14.01       
Worst 1% Hourly Move      | -1.49%       | -1.45%       | -1.18%       | -1.48%      
Autocorr(r^2) Pooled      | 0.1775       | 0.1729       | -0.0055      | 0.0588      
Autocorr(r^2) Mean Path   | 0.1775       | 0.1585       | -0.0083      | 0.0595      

================================================================================
 CALIBRATION ACCEPTANCE TABLE (SPEC §7)
================================================================================
Generator          | Metric                 | Simulated    | Tolerance              | Status
--------------------------------------------------------------------------------------------
Bootstrap          | Mean Return            | +0.000065    | |mean| < 5e-4          | PASS
Bootstrap          | Std (Vol)              | +0.004887    | 0.004918 +/- 15%       | PASS
Bootstrap          | Skewness               | -0.023922    | [-1.0, +0.5]           | PASS
Bootstrap          | Excess Kurtosis        | +12.23       | kurt >= 8.0            | PASS
Bootstrap          | Worst 1% Move          | -0.014541    | [-2.0%, -1.0%]         | PASS
Bootstrap          | Vol Clustering (r^2)   | +0.16        | 0.1775 +/- 0.08        | PASS
Jump Diffusion     | Mean Return            | -0.000011    | |mean| < 5e-4          | PASS
Jump Diffusion     | Std (Vol)              | +0.004913    | 0.004918 +/- 15%       | PASS
Jump Diffusion     | Skewness               | -0.95        | [-1.5, +0.2]           | PASS
Jump Diffusion     | Excess Kurtosis        | +6.74        | kurt >= 4.0            | PASS
Jump Diffusion     | Worst 1% Move          | -0.011782    | [-2.0%, -1.0%]         | PASS
Jump Diffusion     | Vol Clustering (r^2)   | -0.008340    | 0.1775 +/- 0.08        | FAIL
Regime-Switching   | Mean Return            | -0.000018    | |mean| < 5e-4          | PASS
Regime-Switching   | Std (Vol)              | +0.004888    | 0.004918 +/- 15%       | PASS
Regime-Switching   | Skewness               | -0.39        | [-1.0, +0.5]           | PASS
Regime-Switching   | Excess Kurtosis        | +14.01       | kurt >= 4.0            | PASS
Regime-Switching   | Worst 1% Move          | -0.014800    | [-2.0%, -1.0%]         | PASS
Regime-Switching   | Vol Clustering (r^2)   | +0.059546    | 0.1775 +/- 0.08        | FAIL
================================================================================
 CALIBRATION VERDICT & STATUS (SPEC §7)
================================================================================
>> Historical Block Bootstrap (Primary): ALL 6 CHECKS PASSED. Accepted as primary generator.
>> Jump Diffusion (Robustness Panel 1): 5/6 CHECKS PASSED. Vol clustering MISSED (-0.0083 vs 0.1775)
   due to memoryless Poisson arrivals. Retained as labelled robustness panel for jump risk.
>> Regime-Switching (Robustness Panel 2): 5/6 CHECKS PASSED. Vol clustering MISSED (0.0595 vs 0.1775)
   due to Markov state persistence limits. Retained as labelled robustness panel for macro shifts.
>> Overall Verdict: PARTIAL CALIBRATION: Primary generator accepted; robustness misses disclosed.
================================================================================
```

## 2. Tolerance Specification and Known Limitations

SPEC §7 requires simulated paths to match historical hourly volatility, fat tails (excess kurtosis), worst-1% drawdowns, and volatility clustering. The tolerances are evaluated as written, without retroactively widening boundaries.

### 2.1 Historical Block Bootstrap
- **Target alignment:** Resampling 24-hour historical blocks directly replicates empirical multi-hour dependence. Mean path volatility clustering (0.1585) lies comfortably within the empirical acceptance window (0.1775 +/- 0.08). Excess kurtosis (12.23) and worst 1% move (-1.45%) closely replicate historical moments (12.85 and -1.49%).
- **Status:** PASS on all moments.

### 2.2 Jump Diffusion (Merton Jump-Diffusion Variant)
- **Target alignment:** Captures negative skewness (-0.95) and tail kurtosis (6.74) by superimposing Poisson jumps onto continuous Brownian motion. Continuous hourly diffusion sigma is calibrated to 0.448% (annualised 41.9%), with jump intensity lambda = 0.00595 jumps/hour (approx 1 jump per 168 hours).
- **Known limitation (Volatility Clustering Miss):** Jump Diffusion exhibits near-zero volatility clustering (-0.0083 vs realised 0.1775). Because jump arrivals are governed by a memoryless Poisson process and continuous increments are i.i.d. Gaussian, Jump Diffusion cannot produce volatility clustering. If subjected to the historical clustering target (0.1775 +/- 0.08), it misses. This is an intrinsic architectural limitation of Poisson jump-diffusion processes, reported plainly as a known limitation.

### 2.3 Regime-Switching (3-State Markov Chain)
- **Target alignment:** Hourly regime sigmas are calibrated to:
  - Low-volatility regime: 0.31% hourly (29.4% annualised)
  - Medium-volatility regime: 0.55% hourly (51.8% annualised)
  - High-volatility regime: 0.95% hourly (89.2% annualised)
  The generator accurately captures high excess kurtosis (14.01) and worst 1% drawdown (-1.48%).
- **Known limitation (Clustering Attenuation):** While Markov transition persistence produces positive lag-1 autocorrelation of squared returns (0.0588 pooled, 0.0595 mean path), this value falls substantially below historical realized clustering (0.1775). Under a strict empirical match criteria (0.1775 +/- 0.08), the regime model misses. Intra-regime returns are drawn from zero-mean empirical or Gaussian pools without intra-regime autoregressive heteroskedasticity (GARCH), dampening path-level clustering.

## 3. Methodological Conclusion

The Historical Block Bootstrap serves as the primary simulation engine for the parameter sweep because it simultaneously satisfies all empirical moment and path-dependency constraints without parametric distortion. The Jump Diffusion and Regime-Switching engines are preserved strictly as robustness panels to evaluate sensitivity against alternative jump frequencies and prolonged stress regimes.
