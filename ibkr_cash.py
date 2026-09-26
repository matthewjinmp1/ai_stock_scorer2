"""Read cash from local TWS. No order submission methods are called."""
import json
import threading
import uuid
from decimal import Decimal, ROUND_DOWN


def cash_budget(values):
    accounts = {account for account, tag, currency in values}
    if len(accounts) != 1:
        raise ValueError("All cash requires exactly one account connected to TWS.")
    account = next(iter(accounts))
    amounts = []
    for tag in ("CashBalance", "AvailableFunds"):
        raw = values.get((account, tag, "USD"))
        if raw is None:
            raise ValueError("USD cash and available funds were not returned. Use a manual budget.")
        value = Decimal(raw)
        if not value.is_finite():
            raise ValueError("TWS returned an invalid balance.")
        amounts.append(value)
    return {"cash": str(max(Decimal(0), min(amounts)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)), "currency": "USD"}


def read_cash():
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper

    class Reader(EWrapper, EClient):
        def __init__(self):
            EClient.__init__(self, self)
            self.ready = threading.Event()
            self.done = threading.Event()
            self.values = {}

        def nextValidId(self, orderId):
            self.ready.set()

        def error(self, reqId, errorCode, errorString, *args):
            pass  # Connection/request deadlines handle incomplete responses.

        def accountSummary(self, reqId, account, tag, value, currency):
            self.values[(account, tag.removeprefix("$LEDGER-"), currency)] = value

        def accountSummaryEnd(self, reqId):
            self.done.set()

    client = Reader()
    try:
        client.connect("127.0.0.1", 7496, clientId=100000 + uuid.uuid4().int % 1000000)
        threading.Thread(target=client.run, daemon=True).start()
        if not client.ready.wait(10):
            raise ValueError("Open TWS, enable its read-only API on port 7496, and approve the connection.")
        client.reqAccountSummary(1, "All", "AvailableFunds,$LEDGER:USD")
        if not client.done.wait(10):
            raise ValueError("TWS did not return a complete cash balance. Try again.")
        client.cancelAccountSummary(1)
        return cash_budget(client.values)
    finally:
        client.disconnect()


if __name__ == "__main__":
    try:
        print(json.dumps(read_cash()))
    except ImportError:
        print(json.dumps({"error": "Install the IBKR client: python3 -m pip install -r requirements.txt"}))
    except Exception as exc:
        print(json.dumps({"error": str(exc) or "Unable to read TWS cash."}))
