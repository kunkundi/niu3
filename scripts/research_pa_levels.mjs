// Research inputs are frozen at the prior close; production uses the same adapter.
import { readFileSync, writeFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { priceActionLevels } from '../web/src/price-action/strategy.js'
import { ENTRY_POLICIES } from '../web/src/price-action/entry-quality.js'

const [inputPath, outputPath, symbols = 'sh588170', policyNames = ENTRY_POLICIES.join(',')] =
  process.argv.slice(2)
const raw = readFileSync(inputPath)
const data = JSON.parse(raw)
const policies = policyNames.split(',')
const output = { input_sha256: createHash('sha256').update(raw).digest('hex'), policies, rows: [] }
for (const instrument of data.instruments) {
  if (symbols !== 'all' && !symbols.split(',').includes(instrument.symbol)) continue
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
    days.push({ day: bars[i].date, plans: Object.fromEntries(policies.map((entryPolicy) =>
      [entryPolicy, priceActionLevels(known, asOf, instrument.tick / 1e6,
        { minimumBars: data.config.minimum_bars, entryPolicy })])) })
  }
  output.rows.push({ symbol: instrument.symbol, days })
  process.stderr.write(`${instrument.symbol}: ${days.length} prior-close decisions\n`)
}
writeFileSync(outputPath, JSON.stringify(output))
