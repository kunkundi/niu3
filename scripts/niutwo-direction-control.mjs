// Applied after the local structure-window and range-lifecycle adaptations.
export function adaptDirectionControl(body) {
  const replace = (pattern, replacement) => {
    if ([...body.matchAll(new RegExp(pattern.source, 'g'))].length !== 1) {
      throw new Error(`Direction-control import anchor changed: ${pattern}`)
    }
    body = body.replace(pattern, replacement)
  }
  replace(
    /function determineAlwaysIn\([\s\S]*?(?=\n\s*function prioritizedStructuralSwings)/,
    `function determineAlwaysIn(directionContext) {
      return summarizeDirection(directionContext)
    }\n`,
  )
  replace(
    /function classifyMarketState\([^)]*\)\s*\{/,
    `function classifyMarketState(bars, trend, legs, classifications, ema20, tradingRanges = [], structureStartIndex = 0, directionContext = null) {
      trend = directionContext?.trend ?? trend`,
  )
  replace(/if \(activeRange\) \{/, 'if (activeRange && !directionContext?.direction) {')
  replace(
    /evidence.push\('高低摆动尚未形成一致方向'\)/,
    "evidence.push(directionContext ? '方向依据与近期价格尚未形成一致方向' : '高低摆动尚未形成一致方向')",
  )
  replace(
    /evidence.push\('摆动保持净方向并包含规律回调'\)/,
    "evidence.push(directionContext?.source === 'breakout' ? '已确认突破保持方向，当前包含回调' : '摆动保持净方向并包含规律回调')",
  )
  replace(
    /Math.abs\(emaSlope\) >= 0.08/,
    "(trend === 'up' ? emaSlope >= 0.08 : emaSlope <= -0.08)",
  )
  replace(
    /(function classifyMarketState\([\s\S]*?)return \{\s*state,\s*label: marketStateLabel\(state\),/,
    `$1const consolidation = Boolean(activeRange && directionContext?.direction)
    if (directionContext) {
      evidence.push(...directionContext.evidence)
      warnings.push(...directionContext.warnings)
    }
    if (consolidation) evidence.push('近期为趋势内整理，已建立的方向依据仍有效')
    return {
      state,
      label: consolidation ? (trend === 'up' ? '上升趋势内整理' : '下降趋势内整理') : marketStateLabel(state),`,
  )
  replace(
    /const marketState = classifyMarketState\(/,
    `const directionContext = buildDirectionContext({ bars, breakouts, ema20, swings: recentSwings, structureStartIndex, atr, tick: DEFAULT_TICK_SIZE })
    const marketState = classifyMarketState(`,
  )
  replace(
    /(const marketState = classifyMarketState\([\s\S]*?structureStartIndex),?(\s*\))/,
    '$1, directionContext$2',
  )
  replace(
    /const alwaysIn = determineAlwaysIn\(marketState, breakouts, barClassifications, structureStartIndex\)/,
    'const alwaysIn = determineAlwaysIn(directionContext)',
  )
  return body
}
