from .base import BaseStrategy
from .nh_sniper import NHSniperStrategy
from .nh_hunter import NHHunterStrategy
from .one_d_rebound import OneDReboundStrategy
from .ma200_trend import MA200TrendStrategy

STRATEGIES = {
    "NH-Hunter":   NHHunterStrategy,
    "NH-Sniper":   NHSniperStrategy,
    "1D-Rebound":  OneDReboundStrategy,
    "200D-Trend":  MA200TrendStrategy,
}

__all__ = [
    "BaseStrategy",
    "NHHunterStrategy",
    "NHSniperStrategy",
    "OneDReboundStrategy",
    "MA200TrendStrategy",
    "STRATEGIES",
]
