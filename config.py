from dataclasses import dataclass, field

@dataclass(frozen=True)
class StrategyConfig:
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
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
    default_capital_cop: float = 1_500_000.0
    default_risk_pct: float = 0.005   # 0,50 %
    max_position_fraction: float = 0.35
    default_cop_per_usdt: float = 3350.0

CFG = StrategyConfig()
