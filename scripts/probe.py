"""Small read-only public source connectivity check; never touches the account database."""

import json

from app.automation.service import history_target
from app.core.calendar import Calendar
from app.core.types import Instrument, now_cn
from app.market_data.providers import PublicProvider


def main():
    provider = PublicProvider()
    now = now_cn()
    target = history_target(Calendar(), now)
    try:
        instrument = provider.profile(Instrument("sh510300", "沪深300ETF"), now)
        quotes = provider.quotes({instrument.symbol: instrument}, now)
        history = provider.history(instrument, target)
        actions = provider.actions(instrument.symbol)
        print(
            json.dumps(
                {
                    "symbol": instrument.symbol,
                    "classified": instrument.tradable,
                    "quote_source": quotes[0].source,
                    "quote_at": quotes[0].at,
                    "quote_stale": not quotes[0].fresh(now),
                    "completed_day": target,
                    "history_source": history["qfq"][-1].source,
                    "history_bars": len(history["qfq"]),
                    "last_bar": history["qfq"][-1].day,
                    "corporate_actions": len(actions),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        provider.close()


if __name__ == "__main__":
    main()
