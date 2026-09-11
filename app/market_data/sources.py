"""User-selected source order, applied independently to each supported data field."""

SOURCE_PRIORITY = ("eastmoney", "tencent", "sina", "ths")
SOURCE_LABELS = {"eastmoney": "东方财富", "tencent": "腾讯财经", "sina": "新浪财经", "ths": "同花顺"}

# The bounded Tencent/Sina daily endpoints do not report historical amount or turnover.
# They supply prices; missing liquidity fields continue to the last capable source.
DAILY_LIQUIDITY_SOURCES = ("eastmoney", "ths")
