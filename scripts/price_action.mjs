import { readFileSync } from 'node:fs'
import { priceActionLevels } from '../web/src/price-action/strategy.js'
const requests = JSON.parse(readFileSync(0, 'utf8'))
process.stdout.write(JSON.stringify(requests.map(({ bars, as_of, tick, minimum_bars }) =>
  priceActionLevels(bars, as_of, tick, { minimumBars: minimum_bars }))))
