from dataclasses import dataclass
from datetime import datetime

@dataclass
class StockSnapshot:
    """Stock analysis results"""
    ticker: str
    name: str
    current_price: float
    change_percent: float
    rsi_value: float
    latest_time: datetime

    @property
    def change_str(self) -> str:
        """Format the price change percentage."""
        icon = '∆' if self.change_percent >= 0 else '∇'
        return f"{icon} {abs(self.change_percent):.2f}%"

    @property
    def latest_time_str(self) -> str:
        """Format the data timestamp."""
        if hasattr(self.latest_time, 'strftime'):
            return self.latest_time.strftime('%m-%d %H:%M')
        return str(self.latest_time)
