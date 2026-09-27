import io
import unittest
import xml.etree.ElementTree as ET
from ibkr_flex import fetch_statement, parse_statement, read_flex

STATEMENT = """<FlexQueryResponse queryName="positions" type="AF"><FlexStatements count="1">
<FlexStatement accountId="U1" fromDate="20260925" toDate="20260925" period="LastBusinessDay">
<OpenPositions>
<OpenPosition accountId="U1" currency="USD" assetCategory="STK" symbol="AMZN" position="0.0105" levelOfDetail="SUMMARY" />
<OpenPosition accountId="U1" currency="USD" assetCategory="STK" symbol="AMZN" position="0.0105" levelOfDetail="LOT" />
<OpenPosition accountId="U1" currency="USD" assetCategory="STK" symbol="BRK B" position="2" levelOfDetail="SUMMARY" />
</OpenPositions>
<CashReport>
<CashReportCurrency accountId="U1" currency="BASE_SUMMARY" endingCash="99" />
<CashReportCurrency accountId="U1" currency="USD" endingCash="43.859" />
</CashReport>
</FlexStatement></FlexStatements></FlexQueryResponse>"""


def opener_for(*bodies):
    calls = []
    def opener(request, timeout):
        calls.append(request.full_url)
        return io.BytesIO(bodies[len(calls) - 1].encode())
    opener.calls = calls
    return opener


class FlexTests(unittest.TestCase):
    def test_parses_summary_positions_and_usd_cash(self):
        result = parse_statement(ET.fromstring(STATEMENT))
        self.assertEqual(result["cash"], "43.85")
        self.assertEqual(result["asOf"], "20260925")
        self.assertEqual(result["positions"], [
            {"symbol": "AMZN", "currency": "USD", "type": "STK", "quantity": "0.0105"},
            {"symbol": "BRK B", "currency": "USD", "type": "STK", "quantity": "2"},
        ])

    def test_missing_sections_and_multiple_accounts_fail(self):
        for xml, message in [
            (STATEMENT.replace("OpenPositions", "Other"), "Open Positions"),
            (STATEMENT.replace('accountId="U1" fromDate', 'accountId="U1" fromDate').replace("</FlexStatement></FlexStatements>", '</FlexStatement><FlexStatement accountId="U2" /></FlexStatements>'), "one account"),
        ]:
            with self.assertRaisesRegex(ValueError, message):
                parse_statement(ET.fromstring(xml))

    def test_polls_until_statement_is_ready(self):
        opener = opener_for(
            "<FlexStatementResponse><Status>Success</Status><ReferenceCode>42</ReferenceCode><Url>https://example.test/GetStatement</Url></FlexStatementResponse>",
            "<FlexStatementResponse><Status>Warn</Status><ErrorCode>1019</ErrorCode><ErrorMessage>Statement generation in progress.</ErrorMessage></FlexStatementResponse>",
            STATEMENT,
        )
        sleeps = []
        result = read_flex("token", "7", include_positions=True, opener=opener, sleep=sleeps.append)
        self.assertEqual(len(result["positions"]), 2)
        self.assertEqual(sleeps, [3])
        self.assertIn("q=42", opener.calls[2])

    def test_cash_only_requires_cash_report_and_hides_positions(self):
        opener = opener_for("<FlexStatementResponse><Status>Success</Status><ReferenceCode>1</ReferenceCode><Url>https://example.test/g</Url></FlexStatementResponse>", STATEMENT)
        self.assertEqual(read_flex("t", "q", opener=opener, sleep=lambda _: None), {"cash": "43.85", "currency": "USD", "asOf": "20260925"})
        no_cash = STATEMENT.replace("CashReport", "Other")
        opener = opener_for("<FlexStatementResponse><Status>Success</Status><ReferenceCode>1</ReferenceCode><Url>https://example.test/g</Url></FlexStatementResponse>", no_cash)
        with self.assertRaisesRegex(ValueError, "Cash Report"):
            read_flex("t", "q", opener=opener, sleep=lambda _: None)

    def test_reports_flex_errors_and_missing_config(self):
        opener = opener_for("<FlexStatementResponse><Status>Fail</Status><ErrorCode>1012</ErrorCode><ErrorMessage>Token has expired.</ErrorMessage></FlexStatementResponse>")
        with self.assertRaisesRegex(ValueError, "Token has expired.*1012"):
            fetch_statement("t", "q", opener=opener)
        with self.assertRaisesRegex(ValueError, "IBKR_FLEX_TOKEN"):
            fetch_statement("", "q")


if __name__ == "__main__":
    unittest.main()
