import unittest
from dataclasses import replace


from app.core.classification import observation_category


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        from app.core.types import Instrument

        self.instrument = Instrument("sh510300", "不能依赖名称的ETF", "equity", "CN", verified=True)

    def test_known_index_types_and_aliases(self):
        examples = {
            "broad": [
                "沪深 300 指数",
                "中证A500指数",
                "上证科创板50成份指数",
                "创业板指数(价格)",
                "深证100指数（价格）",
                "上证科创板综合价格指数",
                "中证沪港深500人民币指数",
                "纳斯达克100指数",
            ],
            "industry": ["中证银行指数", "中证全指半导体产品与设备指数", "中证食品饮料指数"],
            "theme": ["中证人工智能主题指数", "中证半导体材料设备主题指数", "中证机器人指数", "恒生科技指数"],
            "style": ["沪深300价值指数", "上证科创板成长指数"],
            "strategy": [
                "中证800红利低波动指数",
                "中证500质量成长指数",
                "国证自由现金流指数",
                "沪深300ESG策略指数",
            ],
            "other_equity": ["未识别的特殊指数", "自定义300指数"],
        }
        for category, indices in examples.items():
            for index in indices:
                with self.subTest(index=index):
                    result = observation_category(replace(self.instrument, index_id=index))
                    self.assertEqual(result["focus_category"], category)

    def test_market_and_asset_are_separate_and_unknowns_do_not_guess(self):
        overseas = replace(
            self.instrument, category="cross_border", region="OVERSEAS", index_id="恒生创新药指数"
        )
        self.assertEqual(observation_category(overseas)["focus_category"], "theme")
        self.assertEqual(observation_category(overseas)["focus_market"], "OVERSEAS")
        for category in ["bond", "commodity", "money"]:
            self.assertEqual(
                observation_category(replace(self.instrument, category=category))["focus_category"], category
            )
        for instrument in [
            self.instrument,
            replace(overseas, verified=False),
            replace(overseas, region="unknown"),
        ]:
            self.assertEqual(observation_category(instrument)["focus_category"], "unknown")
        # A marketing name cannot turn a known benchmark into another category.
        misleading = replace(self.instrument, name="人工智能主题ETF", index_id="沪深300指数")
        self.assertEqual(observation_category(misleading)["focus_category"], "broad")

if __name__ == "__main__":
    unittest.main()
