from abc import ABC, abstractmethod


class BaseStrategy(ABC):
    name = ""  # 전략 코드명 (예: "NH-Sniper") — 매매일지 기록용

    def __init__(self, broker, gs_manager=None):
        self.broker = broker
        self.gs_manager = gs_manager

    @abstractmethod
    def tick(self, now):
        pass
