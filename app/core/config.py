from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.classification import CATEGORY_LABELS, MARKET_LABELS

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MINIMUM_AMOUNT = Decimal("100000000")
PA_HISTORY_BARS = 250
RETIRED_RISK_FIELDS = {"drawdown_stop"}
RETIRED_EXECUTION_FIELDS = {"slippage_bps"}
RETIRED_STRATEGY_FIELDS = {"retain_rank", "stop_loss", "trailing_stop", "intraday_drift", "intraday_t_trigger"}


def data_dir() -> Path:
    return Path(os.environ.get("NIUNO3_DATA_DIR", ROOT / ".local-data")).expanduser()


class DisplaySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intraday_interval: int = Field(default=10, ge=5, le=60)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    initial_cash: Decimal = Field(default=Decimal("100000"), ge=1000, le=1_000_000_000)
    max_positions: int = Field(default=5, ge=1, le=20)
    max_weight: Decimal = Field(default=Decimal("0.20"), gt=0, le=1)
    max_exposure: Decimal = Field(default=Decimal("0.80"), gt=0, le=1)
    minimum_bars: int = Field(default=120, ge=120, le=250)
    minimum_amount: Decimal = Field(default=DEFAULT_MINIMUM_AMOUNT, ge=0)
    minimum_turnover: Decimal = Field(default=Decimal("0"), ge=0)
    focus_categories: list[str] = Field(default_factory=lambda: list(CATEGORY_LABELS))
    focus_markets: list[str] = Field(default_factory=lambda: list(MARKET_LABELS))
    commission_rate: Decimal = Field(default=Decimal("0.0001"), ge=0, le=Decimal("0.01"))
    minimum_commission: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    participation: Decimal = Field(default=Decimal("0.01"), gt=0, le=Decimal("0.01"))
    quote_max_age: int = Field(default=90, ge=10, le=90)
    coverage_required: Decimal = Field(default=Decimal("0.95"), ge=Decimal("0.8"), le=1)
    market_interval: int = Field(default=60, ge=60, le=600)
    holding_interval: int = Field(default=30, ge=15, le=60)
    execution_mode: Literal["intraday"] = "intraday"
    strategy_model: Literal["price_action"] = "price_action"
    pa_rr_enabled: bool = True
    pa_min_rr: Decimal = Field(default=Decimal("1.5"), ge=1, le=5)
    intraday_confirmations: int = Field(default=2, ge=1, le=5)
    intraday_min_interval: int = Field(default=300, ge=60, le=3600)
    intraday_max_orders: int = Field(default=6, ge=2, le=30)
    intraday_order_ttl: int = Field(default=300, ge=60, le=1800)
    intraday_t_enabled: bool = True
    intraday_t_model: Literal["daily", "minute5"] = "daily"
    intraday_t_fraction: Decimal = Field(default=Decimal("0.25"), gt=0, le=Decimal("0.50"))
    intraday_t_cycles: int = Field(default=2, ge=1, le=5)

    @property
    def history_bars(self) -> int:
        # Eligibility and the background calculation window serve different purposes.
        return PA_HISTORY_BARS

    @classmethod
    def from_record(cls, values: dict):
        """Decode frozen historical fees/settings without restoring retired execution."""
        return cls.model_validate({**values, "strategy_model": "price_action", "execution_mode": "intraday"})

    @model_validator(mode="before")
    @classmethod
    def ignore_retired_fields(cls, values):
        # Historical configurations and frozen orders remain readable without
        # rewriting their evidence. Retired fields no longer affect execution.
        if isinstance(values, dict):
            retired = RETIRED_RISK_FIELDS | RETIRED_EXECUTION_FIELDS | RETIRED_STRATEGY_FIELDS
            return {key: value for key, value in values.items() if key not in retired}
        return values

    @field_validator("focus_categories", "focus_markets")
    @classmethod
    def known_scope(cls, values, info):
        options = CATEGORY_LABELS if info.field_name == "focus_categories" else MARKET_LABELS
        if any(value not in options for value in values):
            raise ValueError("候选观察池包含不支持的分类或市场")
        # Stable config evidence, regardless of selection order or duplicate input.
        return [value for value in options if value in values]

    @model_validator(mode="after")
    def compatible(self):
        if self.max_weight > self.max_exposure:
            raise ValueError("单只上限不得超过总仓上限")
        return self


CONFIG_LABELS = {
    "initial_cash": "初始资金（元）",
    "max_positions": "最大持仓只数",
    "max_weight": "单只仓位上限",
    "max_exposure": "总仓位上限",
    "minimum_bars": "最少日 K 数量",
    "commission_rate": "佣金比例",
    "minimum_commission": "最低佣金（元）",
    "participation": "新增成交量参与率",
    "quote_max_age": "行情有效期（秒）",
    "coverage_required": "数据覆盖率门槛",
    "market_interval": "手动 ETF 行情刷新（秒）",
    "holding_interval": "持仓刷新（秒）",
    "intraday_interval": "分时获取间隔（秒）",
    "execution_mode": "自动执行方式",
    "strategy_model": "买卖策略",
    "pa_rr_enabled": "盈亏比过滤",
    "pa_min_rr": "裸 K 最低潜在盈亏比",
    "intraday_confirmations": "目标连续确认次数",
    "intraday_min_interval": "同一 ETF 最短操作间隔（秒）",
    "intraday_max_orders": "每只 ETF 每日最多订单数",
    "intraday_order_ttl": "盘中未成交订单有效期（秒）",
    "intraday_t_enabled": "底仓做 T",
    "intraday_t_model": "做 T 信号周期",
    "intraday_t_fraction": "每次做 T 使用的持仓比例",
    "intraday_t_cycles": "每只 ETF 每日最多做 T 轮数",
}

# Retained only for decoding old configurations and frozen historical plans.
REMOVED_POOL_FIELDS = {"minimum_amount", "minimum_turnover", "focus_categories", "focus_markets"}
