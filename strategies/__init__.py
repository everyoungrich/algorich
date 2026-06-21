from .base import BaseStrategy
from .nh_sniper import NHSniperStrategy
from .nh_hunter import NHHunterStrategy
from .one_d_rebound import OneDReboundStrategy

STRATEGIES = {
    "NH-Hunter":  NHHunterStrategy,
    "NH-Sniper":  NHSniperStrategy,
    "1D-Rebound": OneDReboundStrategy,
}

__all__ = [
    "BaseStrategy",
    "NHHunterStrategy",
    "NHSniperStrategy",
    "OneDReboundStrategy",
    "STRATEGIES",
]
