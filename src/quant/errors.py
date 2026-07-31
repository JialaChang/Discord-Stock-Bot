class InsufficientDataError(ValueError):
    """Raised when there are too few usable rows to compute indicators or run a backtest.

    Callers surface the message directly to the user, so it must say what to do
    about it (pick a longer period, backfill the ticker) rather than just what failed.
    """
