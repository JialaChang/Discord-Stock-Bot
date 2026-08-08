from dataclasses import dataclass
from datetime import datetime

@dataclass
class StockSnapshot:
    """Stock analysis results"""
    ticker: str
    name: str
    current_price: float
    previous_close: float
    rsi_value: float | None
    latest_time: datetime

    @property
    def change_percent(self) -> float:
        """Change against the previous close. 0.0 when the baseline is unusable."""
        if self.previous_close <= 0:
            return 0.0
        return (self.current_price - self.previous_close) / self.previous_close * 100

    @property
    def change_str(self) -> str:
        """Format the price change percentage."""
        icon = '∆' if self.change_percent >= 0 else '∇'
        return f"{icon} {abs(self.change_percent):.2f}%"

    @property
    def rsi_str(self) -> str:
        """Format the RSI, or 'N/A' when it has no value."""
        return f"{self.rsi_value:.2f}" if self.rsi_value is not None else "N/A"

    @property
    def latest_time_str(self) -> str:
        """Format the data timestamp."""
        if hasattr(self.latest_time, 'strftime'):
            return self.latest_time.strftime('%m-%d %H:%M')
        return str(self.latest_time)
