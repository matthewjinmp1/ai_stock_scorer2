import unittest
from ibkr_cash import cash_budget

class CashTests(unittest.TestCase):
    def test_uses_cash_not_margin_and_respects_available_funds(self):
        for cash, available, expected in [('43.859', '1000', '43.85'), ('100', '20', '20.00'), ('-10', '100', '0.00')]:
            result = cash_budget({('account', 'CashBalance', 'USD'): cash, ('account', 'AvailableFunds', 'USD'): available})
            self.assertEqual(result['cash'], expected)

    def test_incomplete_multiple_accounts_and_non_usd_fail(self):
        for values in [{}, {('a', 'CashBalance', 'USD'): '100'}, {('a', 'CashBalance', 'EUR'): '100', ('a', 'AvailableFunds', 'EUR'): '100'}, {('a', 'CashBalance', 'USD'): '10', ('b', 'AvailableFunds', 'USD'): '10'}, {('a', 'CashBalance', 'USD'): 'NaN', ('a', 'AvailableFunds', 'USD'): '100'}]:
            with self.assertRaises(ValueError):
                cash_budget(values)
