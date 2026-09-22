import unittest

from app.core.config import Settings
from app.core.types import symbol_for


class StrategyTests(unittest.TestCase):

    def test_exchange_and_three_digit_fund_codes(self):
        self.assertEqual(symbol_for("510300"), "sh510300")
        self.assertEqual(symbol_for("159915"), "sz159915")
        self.assertEqual(symbol_for("588000", "sh"), "sh588000")
        with self.assertRaises(ValueError):
            symbol_for("510300", "sz")
        with self.assertRaises(ValueError):
            symbol_for("600000")






    def test_configuration_rejects_invalid_limits(self):
        with self.assertRaises(ValueError):
            Settings(max_weight=".9", max_exposure=".8")
        with self.assertRaises(ValueError):
            Settings(participation=".02")


if __name__ == "__main__":
    unittest.main()
