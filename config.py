from dataclasses import dataclass, field

@dataclass(frozen=True)
class StrategyConfig:
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT")
    primary_symbol: str = "BTCUSDT"

    timeframe_entry: str = "4h"
    timeframe_regime: str = "1d"
    bars_4h: int = 2600
    bars_1d: int = 800

    # Indicadores
    ema_fast: int = 20
    ema_mid: int = 50
    ema_slow: int = 200
    rsi_period: int = 14
    atr_period: int = 14
    volume_period: int = 20
    resistance_lookback: int = 20

    # Setup Pullback
    pullback_rsi_min: float = 44.0
    pullback_rsi_max: float = 62.0
    pullback_vol_ratio_min: float = 0.80
    pullback_max_ema20_atr: float = 0.85

    # Setup Breakout + Retest
    breakout_buffer_atr: float = 0.10
    retest_window_bars: int = 3
    retest_tolerance_atr: float = 0.30
    breakout_rsi_min: float = 50.0
    breakout_rsi_max: float = 72.0
    breakout_vol_ratio_min: float = 0.80

    # Salidas
    reward_risk: float = 2.0
    max_holding_bars: int = 18  # 72 horas
    pullback_stop_atr: float = 1.20
    breakout_stop_atr_below_level: float = 0.70
    stop_buffer_atr: float = 0.10
    # OCO Binance: Limit SL ligeramente por debajo del Stop/Trigger SL.
    stop_limit_buffer_pct: float = 0.0012  # 0,12 %
    # Monitor de posición real: avisar al acercarse a SL/TP.
    position_alert_distance_pct: float = 0.005  # 0,50 %
    min_position_notional_usdt: float = 5.0

    # Costos estimados
    fee_each_side: float = 0.0010       # 0,10 %
    slippage_each_side: float = 0.0002  # 0,02 %

    # Bayes / validación
    beta_alpha: float = 5.0
    beta_beta: float = 5.0
    min_total_trades: int = 40
    min_oos_trades: int = 15
    min_expectancy_r: float = 0.05
    min_profit_factor: float = 1.10
    min_edge_pp: float = 0.0
    oos_fraction: float = 0.40
    oos_folds: int = 4

    # Gestión de riesgo
    default_capital_cop: float = 1_000_000.0
    default_risk_pct: float = 0.012   # 1,20 %
    max_position_fraction: float = 1.00
    default_cop_per_usdt: float = 3350.0

    # V4.1 — recomendación automática
    auto_timeframes: tuple[str, ...] = ("30m", "1h", "2h", "4h")
    auto_history_years: int = 5
    auto_stop_atr: float = 1.25
    auto_reward_risk: float = 2.0
    auto_bootstrap_sims: int = 2000
    auto_max_entry_drift_atr: float = 0.50
    auto_futures_leverage_promising: int = 2
    auto_futures_leverage_strong: int = 3

    # V4.2 — modo balanceado para aumentar frecuencia sin forzar trades
    auto_balanced_min_test_trades: int = 50
    auto_balanced_min_expectancy_r: float = 0.05
    auto_balanced_min_profit_factor: float = 1.08
    auto_balanced_min_prob_positive: float = 0.75
    auto_balanced_max_candidates_per_symbol: int = 2

    # Fibonacci V4.2 Quant: confluencia opcional, seleccionada solo con VALIDACIÓN.
    fib_min_validation_trades: int = 20
    fib_min_validation_score_gain: float = 0.03
    fib_min_validation_expectancy_gain_r: float = 0.02

    # Ejecución de alertas: evita perseguir el precio.
    alert_enter_now_atr: float = 0.15
    alert_limit_max_atr: float = 0.50
    alert_min_current_rr: float = 1.80

CFG = StrategyConfig()
