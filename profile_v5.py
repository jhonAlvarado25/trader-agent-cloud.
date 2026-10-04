"""One validated capital/risk profile. No credentials and no trading permissions."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import math
import os
from pathlib import Path

FIXED_RISK_PCT = 0.015

@dataclass(frozen=True)
class TradingProfile:
    capital_cop: float = 1_000_000.0
    available_cop: float = 1_000_000.0
    cop_per_usdt: float = 3350.0  # Assumption only: confirm your actual purchase rate.
    fx_confirmed: bool = False
    risk_pct: float = FIXED_RISK_PCT
    max_open_risk_pct: float = FIXED_RISK_PCT
    committed_risk_cop: float = 0.0
    max_position_fraction: float = 1.0
    mode: str = "PAPER"
    target_ea_min: float = 0.15
    target_ea_max: float = 0.20
    cdt_ea: float | None = None
    max_daily_loss_pct: float = 0.015
    max_weekly_loss_pct: float = 0.03
    max_drawdown_pct: float = 0.05
    spot_fee_each_side: float = 0.001
    futures_fee_each_side: float = 0.0005
    slippage_each_side: float = 0.0003
    futures_leverage: int = 2

    def __post_init__(self):
        for key, value in asdict(self).items():
            if key in {"mode", "fx_confirmed", "cdt_ea"}:
                continue
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"Valor no finito en {key}")
        if self.capital_cop <= 0 or self.cop_per_usdt <= 0:
            raise ValueError("Capital y tasa COP/USDT deben ser positivos")
        if not 0 <= self.available_cop <= self.capital_cop:
            raise ValueError("El capital disponible debe estar entre cero y el capital total")
        if not 0.0005 <= self.risk_pct <= FIXED_RISK_PCT:
            raise ValueError("Riesgo interno permitido: hasta 1,5% del capital")
        if not self.risk_pct <= self.max_open_risk_pct <= 0.02:
            raise ValueError("Límite conjunto de riesgo inválido")
        if self.committed_risk_cop < 0 or not 0 < self.max_position_fraction <= 1:
            raise ValueError("Riesgo comprometido/asignación inválidos")
        if self.mode not in {"PAPER", "PILOTO_MANUAL"} or type(self.fx_confirmed) is not bool:
            raise ValueError("Modo o confirmación de tasa inválidos")
        if self.futures_leverage not in (1, 2):
            raise ValueError("V5 limita el apalancamiento a 1x/2x")
        if not 0 <= self.target_ea_min <= self.target_ea_max <= 1:
            raise ValueError("Meta EA inválida")
        if self.cdt_ea is not None and (not math.isfinite(self.cdt_ea) or not 0 <= self.cdt_ea <= 1):
            raise ValueError("Benchmark CDT inválido")
        for x in (self.spot_fee_each_side, self.futures_fee_each_side, self.slippage_each_side):
            if not 0 <= x <= 0.02:
                raise ValueError("Costo por lado inválido")
        for x in (self.max_daily_loss_pct, self.max_weekly_loss_pct, self.max_drawdown_pct):
            if not 0 < x <= 0.20:
                raise ValueError("Límite de pérdida inválido")

    @property
    def risk_budget_cop(self):
        remaining = self.capital_cop * self.max_open_risk_pct - self.committed_risk_cop
        return max(0.0, min(self.capital_cop * self.risk_pct, remaining))

    def updated(self, **values):
        return replace(self, **values)


def load_profile(path: str | Path = "trading_profile.json", environ: dict | None = None):
    data = json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}
    if not isinstance(data, dict):
        raise ValueError("El perfil debe ser un objeto JSON")
    if set(data) - set(TradingProfile.__dataclass_fields__):
        raise ValueError("El perfil contiene campos desconocidos")
    env = os.environ if environ is None else environ
    mapping = {
        "TRADER_CAPITAL_COP": "capital_cop", "TRADER_AVAILABLE_COP": "available_cop",
        "TRADER_COP_PER_USDT": "cop_per_usdt",
        "TRADER_COMMITTED_RISK_COP": "committed_risk_cop",
    }
    for name, field in mapping.items():
        if env.get(name, "") != "":
            data[field] = float(env[name])
    if env.get("TRADER_CAPITAL_COP") and not env.get("TRADER_AVAILABLE_COP"):
        data["available_cop"] = data["capital_cop"]
    if env.get("TRADER_MODE"):
        data["mode"] = env["TRADER_MODE"].upper()
    if env.get("TRADER_FX_CONFIRMED"):
        if env["TRADER_FX_CONFIRMED"].lower() not in {"true", "false"}:
            raise ValueError("TRADER_FX_CONFIRMED debe ser true/false")
        data["fx_confirmed"] = env["TRADER_FX_CONFIRMED"].lower() == "true"
    # User policy V5.1: neither old JSON files nor environment variables change it.
    data.update(risk_pct=FIXED_RISK_PCT, max_open_risk_pct=FIXED_RISK_PCT)
    return TradingProfile(**data)


def profile_json(profile: TradingProfile):
    return json.dumps(asdict(profile), ensure_ascii=False, indent=2, allow_nan=False)


def goal_summary(profile: TradingProfile):
    return {
        "annual_gain_min_cop": profile.capital_cop * profile.target_ea_min,
        "annual_gain_max_cop": profile.capital_cop * profile.target_ea_max,
        "monthly_rate_min": (1 + profile.target_ea_min) ** (1/12) - 1,
        "monthly_rate_max": (1 + profile.target_ea_max) ** (1/12) - 1,
        "cdt_gain_cop": None if profile.cdt_ea is None else profile.capital_cop * profile.cdt_ea,
    }
