// Explicit local import; no runtime dependency on the sibling project.
import { readFileSync, writeFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { stripTypeScriptTypes } from 'node:module'
import { resolve } from 'node:path'
import { adaptStructureWindow, adaptCurrentState, currentStateImport } from './niutwo-current-state.mjs'

const sourceRoot = resolve(process.argv[2] || '../NiuTwo')
const target = resolve('web/src/price-action')
const readSource = (name) => readFileSync(resolve(sourceRoot, 'miniprogram/utils', name), 'utf8')
const banner = (name, source) => `// Imported from NiuTwo/miniprogram/utils/${name}.\n// Source SHA-256: ${createHash('sha256').update(source).digest('hex')}\n// Reproduce with: node scripts/import_niutwo.mjs /path/to/NiuTwo\n`
const core = readSource('price-action.ts')
const names = [...core.matchAll(/^export (?:function|const) (\w+)/gm)].map((match) => match[1])
let body = stripTypeScriptTypes(core, { mode: 'strip' })
  .replace(/^export /gm, '')
  .replace('const DEFAULT_TICK_SIZE = 0.01', 'const DEFAULT_TICK_SIZE = tickSize')
  .replace('const DEFAULT_MICRO_CHANNEL_TICK_SIZE = 0.01', 'const DEFAULT_MICRO_CHANNEL_TICK_SIZE = tickSize')
  .replace('Math.max(0.01, Math.max(Math.abs(left), Math.abs(right)) * 0.0002)', 'Math.max(tickSize, Math.max(Math.abs(left), Math.abs(right)) * 0.0002)')
  .replace('Math.max(atr, currentPrice * 0.0025, 0.01)', 'Math.max(atr, currentPrice * 0.0025, tickSize)')
if (body.includes('const DEFAULT_TICK_SIZE = 0.01')) throw new Error('Tick-size adaptation failed')
body = adaptCurrentState(adaptStructureWindow(body))
writeFileSync(resolve(target, 'engine.js'), banner('price-action.ts', core) +
  currentStateImport +
  `\nexport function createPriceActionEngine(tickSize = 0.01) {\n` +
  `if (!Number.isFinite(tickSize) || tickSize <= 0) throw new Error('Invalid tick size')\n` + body +
  `\nreturn { ${names.join(', ')} }\n}\n`)

const chart = readSource('kline-chart.ts')
let renderer = stripTypeScriptTypes(chart.replace('import { KlineBar }', 'import type { KlineBar }')
  .replace('import { PriceActionAnalysis }', 'import type { PriceActionAnalysis }'), { mode: 'strip' })
const start = renderer.indexOf('  if (showAnalysisOverlays && layers.higherTimeframe)')
const stop = renderer.indexOf('  drawCurrentPrice(context, chart, bars, colors)', start)
if (start < 0 || stop < 0) throw new Error('Overlay entry point was not found')
let overlays = renderer.slice(start, stop)
  .replace(/  if \(layers.volume\) \{\n    drawVolumes\(context, chart, bars, colors\)\n  \}\n/, '')
  .replace('  drawCandles(context, chart, bars, colors, riseCandleStyle)\n', '')
  .replace('drawTrendLines(context, chart, analysis, colors, barOffset, bars.length, layers.trendChannels)',
    'drawTrendLines(context, chart, layers.trendLines ? analysis : { ...analysis, trendLines: [] }, colors, barOffset, bars.length, layers.trendChannels)')
renderer += `\n// Web overlay adapter: caller owns candles, axes, volume and pointer interaction.\nexport function drawPriceActionLayers(context, bars, analysis, chart, theme = 'light', barOffset = 0, layers = {}, palette = {}) {\nconst colors = { ...COLOR_THEMES[theme], ...palette }\nconst showAnalysisOverlays = true\n${overlays}\n}\n`
writeFileSync(resolve(target, 'renderer.js'), banner('kline-chart.ts', chart) + renderer)
writeFileSync(resolve('web/tests/fixtures/niutwo-price-action.json'), readFileSync(resolve(sourceRoot, 'tests/fixtures/price-action-golden.json')))
