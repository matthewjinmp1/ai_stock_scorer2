"""One-time basket activation at 10:30 Eastern on the next NYSE trading day."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def next_basket_schedule(now=None):
    import holidays
    eastern = ZoneInfo("America/New_York")
    now = (now or datetime.now(eastern)).astimezone(eastern)
    day = now.date() + timedelta(days=1)
    closures = holidays.financial_holidays("NYSE", years=[day.year, day.year + 1])
    while day.weekday() >= 5 or day in closures:
        day += timedelta(days=1)
    activation = datetime(day.year, day.month, day.day, 10, 30, tzinfo=eastern)
    return {"goodAfter": activation.strftime("%Y%m%d %H:%M:%S US/Eastern"),
            "scheduledAt": activation.isoformat(),
            "label": activation.strftime("%A, %B %d, %Y at 10:30 a.m. Eastern")}
