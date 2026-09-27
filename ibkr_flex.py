"""Read positions and cash from an IBKR Flex Query. Flex is reporting-only and cannot place orders."""
import time
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SEND_REQUEST_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
STATEMENT_IN_PROGRESS = {"1019"}


def _get(url, params, opener):
    request = Request(f"{url}?{urlencode(params)}", headers={"User-Agent": "ai-stock-scorer/1.0"})
    with opener(request, timeout=30) as response:
        return response.read().decode("utf-8")


def _flex_error(root):
    message = (root.findtext("ErrorMessage") or "").strip()
    code = (root.findtext("ErrorCode") or "").strip()
    return ValueError(f"IBKR Flex: {message or 'request failed'}{f' (code {code})' if code else ''}")


def fetch_statement(token, query_id, opener=urlopen, sleep=time.sleep, attempts=20):
    if not token or not query_id:
        raise ValueError("Set IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID in .env, then restart the app.")
    root = ET.fromstring(_get(SEND_REQUEST_URL, {"t": token, "q": query_id, "v": "3"}, opener))
    if (root.findtext("Status") or "").strip() != "Success":
        raise _flex_error(root)
    reference, url = root.findtext("ReferenceCode"), root.findtext("Url")
    if not reference or not url:
        raise ValueError("IBKR Flex returned an incomplete response. Try again.")
    for _ in range(attempts):
        text = _get(url.strip(), {"t": token, "q": reference.strip(), "v": "3"}, opener)
        root = ET.fromstring(text)
        if root.tag == "FlexQueryResponse":
            return root
        if (root.findtext("ErrorCode") or "").strip() not in STATEMENT_IN_PROGRESS:
            raise _flex_error(root)
        sleep(3)
    raise ValueError("IBKR Flex is still generating the report. Try again in a minute.")


def parse_statement(root):
    statements = root.findall("./FlexStatements/FlexStatement")
    if len({statement.get("accountId") for statement in statements}) != 1:
        raise ValueError("The Flex Query must cover exactly one account.")
    statement = statements[0]
    section = statement.find("OpenPositions")
    if section is None:
        raise ValueError("Add the Open Positions section to your Flex Query.")
    positions = []
    for row in section.findall("OpenPosition"):
        # Lot-level rows repeat the summary quantity.
        if row.get("levelOfDetail", "SUMMARY").upper() != "SUMMARY":
            continue
        try:
            quantity = Decimal(row.get("position", ""))
        except InvalidOperation:
            raise ValueError(f"IBKR Flex returned an invalid quantity for {row.get('symbol')}.")
        positions.append({"symbol": row.get("symbol", ""), "currency": row.get("currency", ""), "type": row.get("assetCategory", ""), "quantity": str(quantity)})
    cash = None
    for row in statement.findall("./CashReport/CashReportCurrency"):
        if row.get("currency") == "USD":
            try:
                value = Decimal(row.get("endingCash", ""))
            except InvalidOperation:
                raise ValueError("IBKR Flex returned an invalid cash balance.")
            cash = str(max(Decimal(0), value).quantize(Decimal("0.01"), rounding=ROUND_DOWN))
    return {"positions": positions, "cash": cash, "currency": "USD", "asOf": statement.get("toDate")}


def read_flex(token, query_id, include_positions=False, **kwargs):
    result = parse_statement(fetch_statement(token, query_id, **kwargs))
    if include_positions:
        return result
    if result["cash"] is None:
        raise ValueError("Add the Cash Report section to your Flex Query.")
    result.pop("positions")
    return result
