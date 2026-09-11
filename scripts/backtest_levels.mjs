// Frozen daily research inputs. Each day uses only the preceding completed bars.
import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { priceActionLevels } from "../web/src/price-action/strategy.js";

const [inputPath, outputPath] = process.argv.slice(2);
if (!inputPath || !outputPath)
  throw new Error(
    "Usage: node scripts/backtest_levels.mjs input.json levels.json",
  );
const raw = readFileSync(inputPath, "utf8");
const input = JSON.parse(raw);
const rows = [];
for (const item of input.etfs) {
  const bars = item.qfq.map((bar) => ({
    date: bar.day,
    closed: true,
    ...Object.fromEntries(
      ["open", "high", "low", "close"].map((key) => [key, Number(bar[key])]),
    ),
  }));
  const days = [];
  for (let index = input.config.minimum_bars; index < bars.length; index++) {
    const known = bars.slice(Math.max(0, index - 250), index);
    const asOf = known.at(-1).date;
    const tick = item.instrument.tick / 1e6;
    const options = { minimumBars: input.config.minimum_bars };
    days.push({
      day: bars[index].date,
      known_through: asOf,
      legacy: priceActionLevels(known, asOf, tick, options),
      current: priceActionLevels(known, asOf, tick, {
        ...options,
        directionMode: "current",
      }),
    });
  }
  rows.push({ symbol: item.instrument.symbol, days });
  process.stderr.write(
    `${item.instrument.symbol}: ${days.length} eligible days\n`,
  );
}
writeFileSync(
  outputPath,
  JSON.stringify({
    input_sha256: createHash("sha256").update(raw).digest("hex"),
    rows,
  }),
);
