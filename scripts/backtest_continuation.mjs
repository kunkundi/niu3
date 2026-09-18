// Entry-only diagnostic: baseline/candidate share the same exits in Python.
import { readFileSync, writeFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { priceActionLevels } from '../web/src/price-action/strategy.js'

const [inputPath, outputPath] = process.argv.slice(2)
const raw = readFileSync(inputPath)
const data = JSON.parse(raw)
const output = { input_sha256: createHash('sha256').update(raw).digest('hex'), rows: [] }
for (const instrument of data.instruments) {
  const bars = data.bars
    .filter((r) => r.symbol === instrument.symbol && r.adjustment === 'qfq')
    .map((r) => JSON.parse(r.payload))
    .sort((a, b) => a.day.localeCompare(b.day))
    .map((b) => ({ date: b.day, closed: true, ...Object.fromEntries(
      ['open', 'high', 'low', 'close'].map((k) => [k, Number(b[k])])) }))
  const days = []
  for (let i = data.config.minimum_bars; i < bars.length; i++) {
    const known = bars.slice(Math.max(0, i - 250), i)
    const asOf = known.at(-1).date
    const options = { minimumBars: data.config.minimum_bars }
    days.push({ day: bars[i].date, baseline: priceActionLevels(known, asOf, instrument.tick / 1e6,
      { ...options, continuation: false }), candidate: priceActionLevels(known, asOf, instrument.tick / 1e6,
        { ...options, continuation: true }) })
  }
  output.rows.push({ symbol: instrument.symbol, days })
  process.stderr.write(`${instrument.symbol}: ${days.length} days\n`)
}
writeFileSync(outputPath, JSON.stringify(output))
