from .base import BaseStrategy
from .nh_sniper import NHSniperStrategy
from .one_d_rebound import OneDReboundStrategy

STRATEGIES = {
    "NH-Sniper":  NHSniperStrategy,
    "1D-Rebound": OneDReboundStrategy,
}

__all__ = ["BaseStrategy", "NHSniperStrategy", "OneDReboundStrategy", "STRATEGIES"]
