import { adaptDirectionControl } from './niutwo-direction-control.mjs'

// Reproduce the existing local structure window before applying current-state selection.
export function adaptStructureWindow(body) {
  const patches = [
    ['const structuralSwings = prioritizedStructuralSwings(swings)', `const structureStartIndex = Math.max(0, bars.length - (options.structureLookback ?? bars.length))
    const recentSwings = options.structureLookback
      ? labelSwings(swings.filter((point) => point.index >= structureStartIndex), bars)
      : swings
    const structuralSwings = prioritizedStructuralSwings(recentSwings)`],
    ['const levels = clusterLevels(swings, currentPrice, atr)', `const levels = clusterLevels(recentSwings, currentPrice, atr)
    const background = options.structureLookback
      ? { trend: determineTrend(prioritizedStructuralSwings(swings)).trend,
          levels: clusterLevels(swings, currentPrice, atr), startDate: bars[0].date, count: bars.length }
      : null`],
    ['const signals = findCandleSignals(bars, swings)', 'const signals = findCandleSignals(bars, recentSwings)'],
    ['const confidence = calculateCompleteness(swings, levels, trendResult)', 'const confidence = calculateCompleteness(recentSwings, levels, trendResult)'],
    ['const invalidation = buildInvalidation(trendResult.trend, swings, levels, currentPrice)', 'const invalidation = buildInvalidation(trendResult.trend, recentSwings, levels, currentPrice)'],
    ['trend: trendResult.trend,', 'structureStartIndex, recentSwings, background, trend: trendResult.trend,'],
  ]
  for (const [anchor, replacement] of patches) {
    if (body.split(anchor).length !== 2) throw new Error(`Structure-window import anchor changed: ${anchor}`)
    body = body.replace(anchor, replacement)
  }
  return body
}

// Keep the local current-state correction reproducible when importing NiuTwo.
export function adaptCurrentState(body) {
  const replace = (pattern, replacement) => {
    if ([...body.matchAll(new RegExp(pattern.source, 'g'))].length !== 1) {
      throw new Error(`Current-state import anchor changed: ${pattern}`)
    }
    body = body.replace(pattern, replacement)
  }
  replace(
    /function classifyMarketState\([^)]*\)/,
    'function classifyMarketState(bars, trend, legs, classifications, ema20, tradingRanges = [], structureStartIndex = 0)',
  )
  replace(
    /\.find\(\(leg\) => leg.direction === \(direction === 'bullish' \? 'bull' : 'bear'\)\)/,
    ".find((leg) => leg.startIndex >= structureStartIndex && leg.direction === (direction === 'bullish' ? 'bull' : 'bear'))",
  )
  replace(
    /const activeRange = tradingRanges[\s\S]*?(?=\s*if \(activeRange\))/,
    'const activeRange = selectCurrentTradingRange(tradingRanges, bars, structureStartIndex)',
  )
  replace(
    /function findTradingRanges\([^)]*\)/,
    'function findTradingRangeCandidates(bars, swings, classifications)',
  )
  replace(
    /(function findTradingRangeCandidates\([\s\S]*?)const sorted = candidates.sort\(/,
    `$1return candidates
  }

  function findTradingRanges(bars, swings, classifications, candidates = findTradingRangeCandidates(bars, swings, classifications)) {
    const sorted = candidates.slice().sort(`,
  )
  replace(
    /const tradingRanges = findTradingRanges\(bars, swings, barClassifications\)/,
    `const rangeCandidates = findTradingRangeCandidates(bars, swings, barClassifications)
    const tradingRanges = findTradingRanges(bars, swings, barClassifications, rangeCandidates)
    const activeTradingRange = selectCurrentTradingRange(rangeCandidates, bars, structureStartIndex)`,
  )
  replace(
    /(const marketState = classifyMarketState\([\s\S]*?tradingRanges),?(\s*\))/,
    '$1, structureStartIndex$2',
  )
  replace(
    /(const marketState = classifyMarketState\([\s\S]*?)tradingRanges,/,
    '$1activeTradingRange ? [activeTradingRange] : [],',
  )
  replace(
    /const alwaysIn = determineAlwaysIn\(marketState, breakouts, barClassifications\)/,
    'const alwaysIn = determineAlwaysIn(marketState, breakouts, barClassifications, structureStartIndex)',
  )
  replace(
    /const activeTradingRange = tradingRanges[\s\S]*?(?=\s*const tradingRangePosition)/,
    '',
  )
  replace(
    /(tradingRangePosition:\s*tradingRangePosition === null\s*\? null\s*: Math.max\(0, Math.min\(1, tradingRangePosition\)\),)/,
    '$1\n      activeTradingRange,',
  )
  return adaptDirectionControl(body)
}

export const currentStateImport = `// Local current-state policy: see scripts/niutwo-current-state.mjs.
import { selectCurrentTradingRange } from './current-context.js'
import { buildDirectionContext, summarizeDirection } from './direction-context.js'\n`
