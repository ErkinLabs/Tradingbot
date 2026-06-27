---
name: Strategy Backtest Baselines
description: Recorded backtest results for each strategy as of their respective run dates — use to detect regressions or improvements
type: project
---

## MACD Strategy — BTC/USDT — 2024-01-01 to 2024-12-31

### Run v7 (Current) — 2026-04-07
**Changes vs v6:** Exit conditions simplified. Entry filters retained (EMA200, ADX>=20, MACD line>0, vol spike 2.0x, RSI 45-75). Tiered exits now: strong_reversal (macd_line < 0 AND curr_hist < 0), three_bar_confirmed (curr_hist < prev_hist < prev2_hist < 0). Removed the momentum_exhaustion and bearish_regime conditions from v6.
**Bars:** 105,408 (5m timeframe)

| Metric | Value |
|--------|-------|
| Total Return | +0.48% |
| Annual Return | +0.48% |
| Max Drawdown | -0.45% |
| DD Duration | 53.3 days |
| Sharpe Ratio | -2.274 |
| Sortino Ratio | -4.557 |
| Calmar Ratio | 1.057 |
| Win Rate | 37.25% |
| Profit Factor | 1.354x |
| Avg Win | +3.1842 USDT |
| Avg Loss | -1.3967 USDT |
| Largest Win | +9.5025 USDT |
| Largest Loss | -4.1357 USDT |
| Avg Hold Time | 1h 7m |
| Total Trades | 51 (19 wins / 32 losses) |
| Total Commission | 18.5142 USDT |
| Final Balance | 3,315.81 USDT (started 3,300 USDT) |

**Why this matters:** Seventh baseline. Compared to v6 (patient exits, longer holds): total return fell slightly (+0.58% → +0.48%), win rate held the same (37.25%), avg hold time dropped from 2h44m → 1h7m, MDD improved (0.91% → 0.45%), Sharpe worsened (-1.442 → -2.274). The simplified exit logic trades some return for lower drawdown and faster exits, but Sharpe suffered.
**How to apply:** v6 patient exits produced better risk-adjusted return on Sharpe but higher MDD. v7 returns to a tighter exit regime. For strategy development: the exit logic is the key variable; test longer hold-to-run vs tighter exit tradeoffs.

---

### Run v6 (Previous) — 2026-04-07
**Changes vs v5:** Exit conditions restructured. Entry filters retained from v5 (EMA200, ADX>20, MACD line>0, vol spike 2x, RSI 45-75). Tiered exits now: macro_reversal (macd_line < 0 AND curr_hist < 0), momentum_exhaustion (RSI > 75 AND hist declining), bearish_regime (RSI < 42 AND hist declining 3 bars). RSI lower exit threshold lowered to 42 (was 45 at entry). Volume spike remains 2.0x.
**Bars:** 105,408 (5m timeframe)

| Metric | Value |
|--------|-------|
| Total Return | +0.58% |
| Annual Return | +0.57% |
| Max Drawdown | -0.91% |
| DD Duration | 183.6 days |
| Sharpe Ratio | -1.442 |
| Sortino Ratio | -3.134 |
| Calmar Ratio | 0.629 |
| Win Rate | 32.00% |
| Profit Factor | 1.305x |
| Avg Win | +5.0825 USDT |
| Avg Loss | -1.8326 USDT |
| Largest Win | +9.5552 USDT |
| Largest Loss | -5.3472 USDT |
| Avg Hold Time | 2h 44m |
| Total Trades | 50 (16 wins / 34 losses) |
| Total Commission | 18.1930 USDT |
| Final Balance | 3,319.01 USDT (started 3,300 USDT) |

**Why this matters:** Sixth baseline. Total return highest ever (+0.58% vs v5 +0.54%). Win rate fell (37.25% → 32.00%) but avg win exploded (+3.13 → +5.08 USDT, +62%). Avg loss grew (+1.30 → +1.83) and max drawdown worsened (0.36% → 0.91%). Sharpe improved significantly (-2.220 → -1.442). Hold time increased dramatically (57m → 2h44m), indicating the new exit logic holds trades longer to let winners run.
**How to apply:** The patient exit strategy traded win rate for avg win size. Net profit improved but risk (MDD, DD duration) increased. Next focus: tightening stop loss or reducing hold time variance to improve Sharpe further.

---

### SL/TP Sweep — 2026-04-07 (4 combos, all on v5 code)

All combos produce identical trade count (51) and win count (19/32) — SL/TP changes only affect fill prices, not signal generation. The strategy is insensitive to the tested SL/TP range.

| Combo | SL% | TP% | Total Ret% | Max DD% | Sharpe | Win% | PF | Sortino | Calmar | Final Bal |
|-------|-----|-----|-----------|---------|--------|------|----|---------|--------|-----------|
| Baseline | 1.5% | 3.0% | +0.54% | 0.36% | -2.220 | 37.25% | 1.425 | -4.977 | 1.493 | $3,317.75 |
| Combo A | 1.2% | 3.5% | +0.55% | 0.40% | -2.095 | 37.25% | 1.419 | -4.593 | 1.362 | $3,318.04 |
| Combo B | 1.0% | 4.0% | +0.48% | 0.38% | -2.355 | 37.25% | 1.371 | -4.999 | 1.252 | $3,315.75 |
| Combo C | 2.0% | 4.0% | +0.50% | 0.36% | -2.340 | 37.25% | 1.394 | -5.111 | 1.384 | $3,316.45 |

**Key finding:** Combo A (SL 1.2% / TP 3.5%) is marginally best on total return (+$18.04 profit vs +$17.75 baseline) and has the best Sharpe (-2.095). Spread between best and worst is only $2.29 across $3,300. SL/TP tuning is not impactful for this strategy; win rate and signal quality are the dominant factors.

---

### Run v5 (Previous) — 2026-04-06
**Changes:** Added `macd_line > 0` filter (double-cross confirmation: fast EMA must be above slow EMA at entry, not just histogram zero-cross).
**Bars:** 105,408 (5m timeframe)

| Metric | Value |
|--------|-------|
| Total Return | +0.54% |
| Annual Return | +0.54% |
| Max Drawdown | -0.36% |
| DD Duration | 53.3 days |
| Sharpe Ratio | -2.220 |
| Sortino Ratio | -4.977 |
| Calmar Ratio | 1.493 |
| Win Rate | 37.25% |
| Profit Factor | 1.425x |
| Avg Win | +3.1301 USDT |
| Avg Loss | -1.3039 USDT |
| Largest Win | +9.5106 USDT |
| Largest Loss | -3.0421 USDT |
| Avg Hold Time | 57m |
| Total Trades | 51 (19 wins / 32 losses) |
| Total Commission | 18.53 USDT |
| Final Balance | 3,317.75 USDT (started 3,300 USDT) |

**Why this matters:** Fifth baseline. MACD line > 0 filter cut trades from 62 → 51 (-18%), crossed the 35% win rate target (37.25%), improved profit factor to 1.425x, slashed max drawdown from 0.59% → 0.36%, and cut DD duration from 92.5 → 53.3 days. Commission drag also fell ($22.48 → $18.53). Sharpe/Sortino still negative but improving.
**How to apply:** Win rate target (35%) is now met. Next focus should be improving Sharpe ratio — negative risk-adjusted return despite positive total return suggests high volatility relative to gains.

---

### Run v4 (Previous) — 2026-04-06
**Changes:** Added ADX > 20 filter to entry condition. Trades only taken when ADX >= 20, confirming a trending market. Intended to eliminate entries in choppy/sideways regimes.
**Bars:** 105,408 (5m timeframe)

| Metric | Value |
|--------|-------|
| Total Return | +0.53% |
| Annual Return | +0.53% |
| Max Drawdown | -0.59% |
| DD Duration | 92.5 days |
| Sharpe Ratio | -1.946 |
| Sortino Ratio | -4.096 |
| Calmar Ratio | 0.905 |
| Win Rate | 33.87% |
| Profit Factor | 1.338x |
| Avg Win | +3.3212 USDT |
| Avg Loss | -1.2712 USDT |
| Largest Win | +9.5443 USDT |
| Largest Loss | -4.3131 USDT |
| Avg Hold Time | 1h 2m |
| Total Trades | 62 (21 wins / 41 losses) |
| Total Commission | 22.48 USDT |
| Final Balance | 3,317.63 USDT (started 3,300 USDT) |

**Why this matters:** Fourth baseline. ADX filter turned the strategy positive for the first time (+0.53%), flipped profit factor above 1.0 (1.338x), cut max drawdown by 57%, halved trade count (123 → 62) and commission drag ($44.33 → $22.48). Win rate still below 35% target (33.87%) but improved from 27.64%.
**How to apply:** Compare future runs against these numbers. The quality/quantity tradeoff was clearly positive. Win rate gap to target (~1.1pp) remains the next focus.

---

### Run v3 (Previous) — 2026-04-06
**Changes:** Exit logic replaced — tiered exit: fast (1 bar, strong_reversal: prev_hist >= 0 and curr_hist < 0 and abs(curr_hist) > abs(prev_hist)) + patient (3 consecutive negative bars). Replaced v2's "2 consecutive negative bars" exit.
**Bars:** 105,408 (5m timeframe)

| Metric | Value |
|--------|-------|
| Total Return | -0.32% |
| Annual Return | -0.32% |
| Max Drawdown | -1.38% |
| DD Duration | 255.4 days |
| Sharpe Ratio | -2.440 |
| Sortino Ratio | -4.282 |
| Calmar Ratio | -0.233 |
| Win Rate | 27.64% |
| Profit Factor | 0.899x |
| Avg Win | +2.7761 USDT |
| Avg Loss | -1.1795 USDT |
| Largest Win | +9.4711 USDT |
| Largest Loss | -5.2785 USDT |
| Avg Hold Time | 1h 2m |
| Total Trades | 123 (34 wins / 89 losses) |
| Total Commission | 44.33 USDT |
| Final Balance | 3,289.41 USDT (started 3,300 USDT) |

**Why this matters:** Third baseline. Tiered exit improved profit factor from 0.831 to 0.899, avg win from +2.495 to +2.776 (+11.3%), Sharpe from -2.765 to -2.440. Still loss-making due to low win rate.
**How to apply:** Compare future backtest runs against these numbers. Win rate (27.64%) remains the core structural issue — exit improvements alone cannot offset a signal filter that generates 72% losers.

---

### Run v2 (Previous) — 2026-04-06
**Changes:** Volume spike multiplier 1.5x → 2.0x; RSI upper bound 70 → 75
**Bars:** 105,408 (5m timeframe)

| Metric | Value |
|--------|-------|
| Total Return | -0.52% |
| Annual Return | -0.52% |
| Max Drawdown | -1.39% |
| DD Duration | 255.4 days |
| Sharpe Ratio | -2.765 |
| Sortino Ratio | -4.757 |
| Calmar Ratio | -0.375 |
| Win Rate | 27.64% |
| Profit Factor | 0.831x |
| Avg Win | +2.4948 USDT |
| Avg Loss | -1.1467 USDT |
| Largest Win | +9.4711 USDT |
| Largest Loss | -5.2759 USDT |
| Avg Hold Time | 1h 2m |
| Total Trades | 123 (34 wins / 157 losses) |
| Total Commission | 44.32 USDT |
| Final Balance | 3,282.77 USDT (started 3,300 USDT) |

**Why this matters:** Second baseline after tightening volume filter and widening RSI band. Still loss-making but metrics improved across the board vs v1.
**How to apply:** Compare future backtest runs against these numbers to assess whether further changes improve performance.

---

### Run v1 (Previous) — 2026-04-06
**Config:** Volume spike 1.5x; RSI upper bound 70
**Bars:** 105,408 (5m timeframe)

| Metric | Value |
|--------|-------|
| Total Return | -1.41% |
| Annual Return | -1.41% |
| Max Drawdown | -1.68% |
| DD Duration | 301.6 days |
| Sharpe Ratio | -2.987 |
| Sortino Ratio | -4.918 |
| Calmar Ratio | -0.837 |
| Win Rate | 27.31% |
| Profit Factor | 0.719x |
| Avg Win | +2.014 USDT |
| Avg Loss | -1.053 USDT |
| Largest Win | +9.473 USDT |
| Largest Loss | -5.245 USDT |
| Avg Hold Time | 1h 3m |
| Total Trades | 216 (59 wins / 157 losses) |
| Total Commission | 77.71 USDT |
| Final Balance | 3,253 USDT (started 3,300 USDT) |
