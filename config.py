from dataclasses import dataclass

@dataclass(frozen=True)
class StrategyConfig:
    symbol: str = "BTCUSDT"
    interval: str = "4h"
    history_bars: int = 3000

    ema_fast: int = 20
    ema_mid: int = 50
    ema_slow: int = 200
    rsi_period: int = 14
    atr_period: int = 14
    volume_period: int = 20

    # Setup LONG: tendencia + retroceso controlado + momentum + volumen
    rsi_min: float = 45.0
    rsi_max: float = 65.0
    min_volume_ratio: float = 0.90
    max_distance_ema20_atr: float = 1.25

    # Stop técnico
    swing_lookback: int = 6
    stop_atr_mult: float = 1.20
    stop_buffer_atr: float = 0.15

    # Objetivo / evaluación
    reward_risk: float = 2.0
    max_holding_bars: int = 18  # 72 h en 4H

    # Estadística
    beta_alpha: float = 5.0
    beta_beta: float = 5.0
    min_backtest_trades: int = 30
    min_expectancy_r: float = 0.10
    min_profit_factor: float = 1.20

    # Riesgo
    default_capital_cop: float = 1_500_000.0
    default_risk_pct: float = 0.005  # 0,5 %
    default_cop_per_usdt: float = 3350.0

CFG = StrategyConfig()
