from pydantic import BaseModel, ConfigDict
from datetime import datetime


class SnapshotResponse(BaseModel):
    """A single stock's latest quote, mirroring `StockSnapshot`"""
    model_config = ConfigDict(from_attributes=True)

    ticker: str
    name: str
    current_price: float
    previous_close: float
    rsi_value: float | None
    latest_time: datetime
    change_percent: float
