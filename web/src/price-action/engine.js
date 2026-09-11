// Imported from NiuTwo/miniprogram/utils/price-action.ts.
// Source SHA-256: 0326df23d4959c5a8b4d51c621e66e868f88b413c746bfdb5de3b67dc5b0a828
// Reproduce with: node scripts/import_niutwo.mjs /path/to/NiuTwo
// Local current-state policy: see scripts/niutwo-current-state.mjs.
import { selectCurrentTradingRange } from './current-context.js'
import { buildDirectionContext, summarizeDirection } from './direction-context.js'

export function createPriceActionEngine(tickSize = 0.01) {
  if (!Number.isFinite(tickSize) || tickSize <= 0) throw new Error('Invalid tick size')

  const SWING_WINDOW_SIZE = 3
  const EMA20_PERIOD = 20
  const EMA20_WARMUP_BAR_COUNT = 100
  const DEFAULT_TICK_SIZE = tickSize
  const TREND_BODY_RATIO = 0.6
  const BULL_TREND_CLOSE_LOCATION = 0.75
  const BEAR_TREND_CLOSE_LOCATION = 0.25
  const DOJI_BODY_RATIO = 0.1
  const TRADING_RANGE_BODY_RATIO = 0.35
  const TRADING_RANGE_OVERLAP_RATIO = 0.55
  const REVERSAL_TAIL_RATIO = 0.35
  const REVERSAL_CLOSE_LOCATION = 0.65
  const MAJOR_SWING_SCORE = 0.75
  const MAJOR_SWING_BALANCED_MOVE_ATR = 2.2
  const MAJOR_SWING_INCOMING_LEG_ATR = 3
  const MAJOR_SWING_DURATION_BARS = 12
  const MAJOR_SWING_RANGE_EXPANSION = 2
  const MAJOR_PRICE_LEG_MAGNITUDE_ATR = 3
  const BREAKOUT_FOLLOW_THROUGH_WINDOW = 3
  const BREAKOUT_MONITOR_WINDOW = 10
  const LEVEL_TOUCH_SEPARATION = SWING_WINDOW_SIZE + 1
  const SIGNAL_CONFIRMATION_WINDOW = 3
  const BREAKOUT_ATR_BUFFER_RATIO = 0.05
  const MICRO_CHANNEL_MIN_LENGTH = 3
  const DEFAULT_MICRO_CHANNEL_TICK_SIZE = tickSize
  const MAX_TREND_LINE_CANDIDATES = 3
  const MAX_CHANNEL_EVENTS = 12

  function closedBarsForAnalysis(bars) {
    return bars.filter((bar) => bar.closed !== false)
  }

  function calculateExponentialMovingAverage(values, period = EMA20_PERIOD) {
    if (!Number.isInteger(period) || period <= 0) {
      throw new Error('EMA 周期必须是正整数')
    }
    const result = Array.from({ length: values.length }, () => null)
    if (values.length < period) {
      return result
    }
    let ema = values.slice(0, period).reduce((total, value) => total + value, 0) / period
    result[period - 1] = ema
    const alpha = 2 / (period + 1)
    for (let index = period; index < values.length; index += 1) {
      ema = alpha * values[index] + (1 - alpha) * ema
      result[index] = ema
    }
    return result
  }

  function emaWarmupBarsForAnalysis(allBars, analysisBarCount, leadingWarmupBars = []) {
    const boundedCount = Math.max(0, Math.min(allBars.length, Math.round(analysisBarCount)))
    const firstAnalysisIndex = allBars.length - boundedCount
    return closedBarsForAnalysis([...leadingWarmupBars, ...allBars.slice(0, firstAnalysisIndex)]).slice(
      -EMA20_WARMUP_BAR_COUNT,
    )
  }

  function analyzeEma20(bars, warmupBars = []) {
    const closedBars = closedBarsForAnalysis(bars)
    const closedWarmupBars = closedBarsForAnalysis(warmupBars).slice(-EMA20_WARMUP_BAR_COUNT)
    const combinedBars = [...closedWarmupBars, ...closedBars]
    const values = calculateExponentialMovingAverage(combinedBars.map((bar) => bar.close))
    const offset = closedWarmupBars.length
    const points = values
      .slice(offset)
      .map((value, index) =>
        value === null
          ? null
          : {
              index,
              knownAtIndex: index,
              value,
            },
      )
      .filter((point) => point !== null)
    const latestPoint = points[points.length - 1]
    const latestBar = closedBars[closedBars.length - 1]
    let latestPosition = 'unavailable'
    if (latestPoint && latestBar) {
      latestPosition =
        latestBar.low <= latestPoint.value && latestBar.high >= latestPoint.value
          ? 'touching'
          : latestBar.close > latestPoint.value
            ? 'above'
            : 'below'
    }
    return {
      period: EMA20_PERIOD,
      points,
      latestValue: latestPoint ? latestPoint.value : null,
      latestPosition,
      warmupBarCount: closedWarmupBars.length,
      preheated: closedWarmupBars.length >= EMA20_WARMUP_BAR_COUNT,
      provenance: 'book-concept',
    }
  }

  function average(values) {
    if (values.length === 0) {
      return 0
    }
    return values.reduce((total, value) => total + value, 0) / values.length
  }

  function median(values) {
    if (values.length === 0) {
      return 0
    }
    const sorted = values.slice().sort((left, right) => left - right)
    const middle = Math.floor(sorted.length / 2)
    return sorted.length % 2 === 0 ? (sorted[middle - 1] + sorted[middle]) / 2 : sorted[middle]
  }

  function clamp01(value) {
    return Math.max(0, Math.min(1, value))
  }

  function barDirectionFromPrices(bar, tickSize) {
    if (bar.close > bar.open + tickSize * 0.5) {
      return 'bullish'
    }
    if (bar.close < bar.open - tickSize * 0.5) {
      return 'bearish'
    }
    return 'neutral'
  }

  function calculateAtomicBarFeatures(bars, tickSize = DEFAULT_TICK_SIZE) {
    const safeTickSize = Math.max(Number.EPSILON, tickSize)
    return bars.map((bar, index) => {
      const range = Math.max(0, bar.high - bar.low)
      const denominator = Math.max(range, safeTickSize)
      const body = Math.abs(bar.close - bar.open)
      const upperTail = Math.max(0, bar.high - Math.max(bar.open, bar.close))
      const lowerTail = Math.max(0, Math.min(bar.open, bar.close) - bar.low)
      const previous = bars[index - 1]
      const overlap = previous
        ? Math.max(0, Math.min(bar.high, previous.high) - Math.max(bar.low, previous.low))
        : 0
      const previousRange = previous ? Math.max(0, previous.high - previous.low) : 0
      const overlapRatio = previous ? overlap / Math.max(Math.min(range, previousRange), safeTickSize) : 0
      const recentMedianRange = median(
        bars
          .slice(Math.max(0, index - 20), index)
          .map((candidate) => Math.max(0, candidate.high - candidate.low)),
      )
      return {
        index,
        range,
        body,
        upperTail,
        lowerTail,
        bodyRatio: body / denominator,
        closeLocation: clamp01((bar.close - bar.low) / denominator),
        overlap,
        overlapRatio: clamp01(overlapRatio),
        relativeRange: recentMedianRange > 0 ? range / recentMedianRange : 1,
        direction: barDirectionFromPrices(bar, safeTickSize),
      }
    })
  }

  function reversalContextScore(bars, index, direction) {
    const previousBars = bars.slice(Math.max(0, index - 3), index)
    if (previousBars.length === 0) {
      return 0
    }
    const bar = bars[index]
    const precedingMove = previousBars[previousBars.length - 1].close - previousBars[0].open
    const againstMove = direction === 'bullish' ? precedingMove < 0 : precedingMove > 0
    const testsExtreme =
      direction === 'bullish'
        ? bar.low <= Math.min(...previousBars.map((candidate) => candidate.low))
        : bar.high >= Math.max(...previousBars.map((candidate) => candidate.high))
    return Number(againstMove) * 0.5 + Number(testsExtreme) * 0.5
  }

  function barClassificationLabel(kind) {
    const labels = {
      'bull-trend': '多头趋势 K 线',
      'bear-trend': '空头趋势 K 线',
      'trading-range': '区间 K 线',
      doji: 'Doji',
      'bull-reversal': '多头反转 K 线',
      'bear-reversal': '空头反转 K 线',
      other: '普通 K 线',
    }
    return labels[kind]
  }

  function classifyPriceBars(bars, options = {}) {
    const tickSize = Math.max(Number.EPSILON, options.tickSize ?? DEFAULT_TICK_SIZE)
    const trendBodyRatio = options.trendBodyRatio ?? TREND_BODY_RATIO
    const bullCloseThreshold = options.bullTrendCloseLocation ?? BULL_TREND_CLOSE_LOCATION
    const bearCloseThreshold = options.bearTrendCloseLocation ?? BEAR_TREND_CLOSE_LOCATION
    const dojiThreshold = options.dojiBodyRatio ?? DOJI_BODY_RATIO
    const rangeBodyThreshold = options.tradingRangeBodyRatio ?? TRADING_RANGE_BODY_RATIO
    const overlapThreshold = options.tradingRangeOverlapRatio ?? TRADING_RANGE_OVERLAP_RATIO
    const features = calculateAtomicBarFeatures(bars, tickSize)

    return features.map((feature) => {
      const isBullTrend =
        feature.direction === 'bullish' &&
        feature.bodyRatio >= trendBodyRatio &&
        feature.closeLocation >= bullCloseThreshold
      const isBearTrend =
        feature.direction === 'bearish' &&
        feature.bodyRatio >= trendBodyRatio &&
        feature.closeLocation <= bearCloseThreshold
      const isTrendBar = isBullTrend || isBearTrend
      const isDoji = feature.bodyRatio <= dojiThreshold
      const tailRatio = (feature.upperTail + feature.lowerTail) / Math.max(feature.range, tickSize)
      const isTradingRangeBar =
        feature.bodyRatio <= rangeBodyThreshold &&
        tailRatio >= 0.45 &&
        (feature.index === 0 || feature.overlapRatio >= overlapThreshold)

      const bullShapeComponents = [
        feature.lowerTail / Math.max(feature.range, tickSize) >= REVERSAL_TAIL_RATIO,
        feature.closeLocation >= REVERSAL_CLOSE_LOCATION,
        feature.lowerTail >= Math.max(feature.body * 1.2, feature.upperTail * 1.2),
      ]
      const bearShapeComponents = [
        feature.upperTail / Math.max(feature.range, tickSize) >= REVERSAL_TAIL_RATIO,
        feature.closeLocation <= 1 - REVERSAL_CLOSE_LOCATION,
        feature.upperTail >= Math.max(feature.body * 1.2, feature.lowerTail * 1.2),
      ]
      const bullShapeScore = bullShapeComponents.filter(Boolean).length / bullShapeComponents.length
      const bearShapeScore = bearShapeComponents.filter(Boolean).length / bearShapeComponents.length
      const bullContextScore = reversalContextScore(bars, feature.index, 'bullish')
      const bearContextScore = reversalContextScore(bars, feature.index, 'bearish')
      const bullishReversal = bullShapeScore >= 2 / 3 && bullContextScore >= 0.5
      const bearishReversal = bearShapeScore >= 2 / 3 && bearContextScore >= 0.5
      const reversalDirection =
        bullishReversal && (!bearishReversal || bullShapeScore >= bearShapeScore)
          ? 'bullish'
          : bearishReversal
            ? 'bearish'
            : null
      const shapeScore =
        reversalDirection === 'bullish'
          ? bullShapeScore
          : reversalDirection === 'bearish'
            ? bearShapeScore
            : Math.max(bullShapeScore, bearShapeScore)
      const contextScore =
        reversalDirection === 'bullish'
          ? bullContextScore
          : reversalDirection === 'bearish'
            ? bearContextScore
            : Math.max(bullContextScore, bearContextScore)

      let kind = 'other'
      if (isDoji) {
        kind = 'doji'
      } else if (reversalDirection === 'bullish') {
        kind = 'bull-reversal'
      } else if (reversalDirection === 'bearish') {
        kind = 'bear-reversal'
      } else if (isBullTrend) {
        kind = 'bull-trend'
      } else if (isBearTrend) {
        kind = 'bear-trend'
      } else if (isTradingRangeBar) {
        kind = 'trading-range'
      }

      const directionCloseScore =
        feature.direction === 'bullish'
          ? feature.closeLocation
          : feature.direction === 'bearish'
            ? 1 - feature.closeLocation
            : 0.5
      const trendScore = clamp01(
        feature.bodyRatio * 0.45 +
          directionCloseScore * 0.3 +
          (1 - feature.overlapRatio) * 0.15 +
          (Math.min(feature.relativeRange, 1.5) / 1.5) * 0.1,
      )
      const rangeScore = clamp01(
        (1 - feature.bodyRatio) * 0.45 + feature.overlapRatio * 0.35 + Math.min(1, tailRatio) * 0.2,
      )
      const reversalScore = clamp01(shapeScore * 0.65 + contextScore * 0.35)
      const score =
        kind === 'bull-trend' || kind === 'bear-trend'
          ? trendScore
          : kind === 'trading-range' || kind === 'doji'
            ? rangeScore
            : kind === 'bull-reversal' || kind === 'bear-reversal'
              ? reversalScore
              : 0
      const evidence = []
      const warnings = []
      if (isTrendBar) evidence.push('实体较大且收盘靠近趋势方向端点')
      if (feature.overlapRatio < 0.35) evidence.push('与前一根 K 线重叠较少')
      if (feature.relativeRange >= 1.15) evidence.push('振幅高于近期中位水平')
      if (isTradingRangeBar) evidence.push('小实体、明显影线且重叠较高')
      if (isDoji) evidence.push('开收盘接近')
      if (reversalDirection) {
        evidence.push(
          reversalDirection === 'bullish' ? '下影拒绝低价并向高位收盘' : '上影拒绝高价并向低位收盘',
        )
        if (contextScore < 1) warnings.push('反转位置背景尚不完整')
      }
      if (kind === 'other') warnings.push('未达到趋势、区间、十字或反转代理阈值')

      return {
        index: feature.index,
        knownAtIndex: feature.index,
        kind,
        label: barClassificationLabel(kind),
        direction: feature.direction,
        features: feature,
        isTrendBar,
        isTradingRangeBar,
        isDoji,
        reversalDirection,
        score,
        shapeScore,
        contextScore,
        evidence,
        warnings,
        provenance: 'implementation-proxy',
      }
    })
  }

  function calculateAtr(bars, period = 14) {
    const ranges = []
    const start = Math.max(0, bars.length - period)
    for (let index = start; index < bars.length; index += 1) {
      const bar = bars[index]
      const previousClose = index > 0 ? bars[index - 1].close : bar.close
      ranges.push(
        Math.max(bar.high - bar.low, Math.abs(bar.high - previousClose), Math.abs(bar.low - previousClose)),
      )
    }
    return average(ranges)
  }

  function comparisonTolerance(left, right) {
    return Math.max(tickSize, Math.max(Math.abs(left), Math.abs(right)) * 0.0002)
  }

  function comparePrices(next, previous) {
    const difference = next - previous
    const tolerance = comparisonTolerance(next, previous)
    if (difference > tolerance) {
      return 1
    }
    if (difference < -tolerance) {
      return -1
    }
    return 0
  }

  function fixedGapLifecycle(bars, startIndex, lowerPrice, upperPrice, direction) {
    let partialFillIndex = null
    for (let index = startIndex + 1; index < bars.length; index += 1) {
      const current = bars[index]
      const tolerance = comparisonTolerance(lowerPrice, upperPrice)
      if (direction === 'bullish') {
        if (partialFillIndex === null && current.low < upperPrice - tolerance) {
          partialFillIndex = index
        }
        if (current.low <= lowerPrice + tolerance) {
          return {
            partialFillIndex: partialFillIndex ?? index,
            filledIndex: index,
            status: 'filled',
            statusKnownAtIndex: index,
          }
        }
      } else {
        if (partialFillIndex === null && current.high > lowerPrice + tolerance) {
          partialFillIndex = index
        }
        if (current.high >= upperPrice - tolerance) {
          return {
            partialFillIndex: partialFillIndex ?? index,
            filledIndex: index,
            status: 'filled',
            statusKnownAtIndex: index,
          }
        }
      }
    }
    return {
      partialFillIndex,
      filledIndex: null,
      status: partialFillIndex === null ? 'active' : 'partially-filled',
      statusKnownAtIndex: partialFillIndex ?? startIndex,
    }
  }

  function priceGapLabel(kind) {
    const labels = {
      'open-gap': 'Open Gap',
      'bar-gap': 'Bar Gap',
      'body-gap': 'Body Gap',
      'breakout-gap': 'Breakout Gap',
      'measuring-gap': 'Measuring Gap',
      'ema-gap-bar': 'MA Gap',
      'twenty-gap-bars': '20 Gap Bars',
    }
    return labels[kind]
  }

  function createFixedGap(
    bars,
    kind,
    startIndex,
    lowerPrice,
    upperPrice,
    direction,
    knownAtIndex = startIndex,
    brokenObjectId,
  ) {
    const lifecycle = fixedGapLifecycle(bars, startIndex, lowerPrice, upperPrice, direction)
    const previousClose = bars[startIndex - 1]?.close ?? bars[startIndex].open
    const gapRatio = (upperPrice - lowerPrice) / Math.max(Math.abs(previousClose), DEFAULT_TICK_SIZE)
    return {
      id: `gap:${kind}:${startIndex}:${lowerPrice.toFixed(4)}:${upperPrice.toFixed(4)}`,
      kind,
      label: priceGapLabel(kind),
      direction,
      startIndex,
      knownAtIndex,
      lowerPrice,
      upperPrice,
      ...lifecycle,
      statusKnownAtIndex: Math.max(knownAtIndex, lifecycle.statusKnownAtIndex),
      ...(brokenObjectId ? { brokenObjectId } : {}),
      evidence: [direction === 'bullish' ? '价格在上方留下未交易区域' : '价格在下方留下未交易区域'],
      warnings: gapRatio >= 0.09 ? ['缺口幅度异常，需核对除权、停牌或涨跌停背景'] : [],
      provenance: kind === 'body-gap' ? 'implementation-proxy' : 'book-concept',
    }
  }

  function emaGapLifecycle(bars, emaByIndex, startIndex) {
    for (let index = startIndex + 1; index < bars.length; index += 1) {
      const ema = emaByIndex.get(index)
      if (ema !== undefined && bars[index].low <= ema && bars[index].high >= ema) {
        return {
          partialFillIndex: index,
          filledIndex: index,
          status: 'filled',
          statusKnownAtIndex: index,
        }
      }
    }
    return {
      partialFillIndex: null,
      filledIndex: null,
      status: 'active',
      statusKnownAtIndex: startIndex,
    }
  }

  /** Detects price voids and preserves when partial/full fills became knowable. */
  function findPriceGaps(bars, ema20, breakouts = []) {
    const gaps = []
    for (let index = 1; index < bars.length; index += 1) {
      const previous = bars[index - 1]
      const current = bars[index]
      if (current.corporateAction) {
        continue
      }
      if (current.open > previous.close + comparisonTolerance(current.open, previous.close)) {
        gaps.push(createFixedGap(bars, 'open-gap', index, previous.close, current.open, 'bullish'))
      } else if (current.open < previous.close - comparisonTolerance(current.open, previous.close)) {
        gaps.push(createFixedGap(bars, 'open-gap', index, current.open, previous.close, 'bearish'))
      }
      if (current.low > previous.high + comparisonTolerance(current.low, previous.high)) {
        gaps.push(createFixedGap(bars, 'bar-gap', index, previous.high, current.low, 'bullish'))
      } else if (current.high < previous.low - comparisonTolerance(current.high, previous.low)) {
        gaps.push(createFixedGap(bars, 'bar-gap', index, current.high, previous.low, 'bearish'))
      }
      const previousBodyLow = Math.min(previous.open, previous.close)
      const previousBodyHigh = Math.max(previous.open, previous.close)
      const currentBodyLow = Math.min(current.open, current.close)
      const currentBodyHigh = Math.max(current.open, current.close)
      if (currentBodyLow > previousBodyHigh + comparisonTolerance(currentBodyLow, previousBodyHigh)) {
        gaps.push(createFixedGap(bars, 'body-gap', index, previousBodyHigh, currentBodyLow, 'bullish'))
      } else if (currentBodyHigh < previousBodyLow - comparisonTolerance(currentBodyHigh, previousBodyLow)) {
        gaps.push(createFixedGap(bars, 'body-gap', index, currentBodyHigh, previousBodyLow, 'bearish'))
      }
    }

    breakouts.forEach((breakout) => {
      if (breakout.closeBreakIndex === null) {
        return
      }
      const base = gaps.find(
        (gap) =>
          gap.startIndex === breakout.closeBreakIndex &&
          gap.direction === breakout.direction &&
          (gap.kind === 'bar-gap' || gap.kind === 'body-gap'),
      )
      if (base) {
        gaps.push(
          createFixedGap(
            bars,
            'breakout-gap',
            base.startIndex,
            base.lowerPrice,
            base.upperPrice,
            base.direction,
            Math.max(base.knownAtIndex, breakout.closeBreakIndex),
            breakout.brokenObjectId,
          ),
        )
      }
    })

    gaps
      .filter((gap) => gap.kind === 'bar-gap' || gap.kind === 'breakout-gap')
      .forEach((gap) => {
        const confirmationIndex = gap.startIndex + 3
        if (
          confirmationIndex < bars.length &&
          (gap.filledIndex === null || gap.filledIndex > confirmationIndex)
        ) {
          gaps.push(
            createFixedGap(
              bars,
              'measuring-gap',
              gap.startIndex,
              gap.lowerPrice,
              gap.upperPrice,
              gap.direction,
              confirmationIndex,
              gap.brokenObjectId,
            ),
          )
        }
      })

    const emaByIndex = new Map(ema20.points.map((point) => [point.index, point.value]))
    let untouchedRunStart = null
    let untouchedRunLength = 0
    ema20.points.forEach((point) => {
      const current = bars[point.index]
      if (!current) return
      const direction = current.low > point.value ? 'bullish' : current.high < point.value ? 'bearish' : null
      if (direction) {
        const lowerPrice = direction === 'bullish' ? point.value : current.high
        const upperPrice = direction === 'bullish' ? current.low : point.value
        const lifecycle = emaGapLifecycle(bars, emaByIndex, point.index)
        gaps.push({
          id: `gap:ema-gap-bar:${point.index}`,
          kind: 'ema-gap-bar',
          label: priceGapLabel('ema-gap-bar'),
          direction,
          startIndex: point.index,
          knownAtIndex: point.index,
          lowerPrice,
          upperPrice,
          ...lifecycle,
          evidence: ['整根 K 线位于 EMA20 同一侧'],
          warnings: [],
          provenance: 'book-concept',
        })
        untouchedRunStart = untouchedRunStart ?? point.index
        untouchedRunLength += 1
        if (untouchedRunLength === 20) {
          const firstTouch = ema20.points.find(
            (candidate) =>
              candidate.index > point.index &&
              (() => {
                const candidateBar = bars[candidate.index]
                return (
                  candidateBar && candidateBar.low <= candidate.value && candidateBar.high >= candidate.value
                )
              })(),
          )
          gaps.push({
            id: `gap:twenty-gap-bars:${untouchedRunStart}`,
            kind: 'twenty-gap-bars',
            label: priceGapLabel('twenty-gap-bars'),
            direction,
            startIndex: untouchedRunStart,
            knownAtIndex: point.index,
            statusKnownAtIndex: firstTouch?.index ?? point.index,
            lowerPrice: Math.min(point.value, current.low),
            upperPrice: Math.max(point.value, current.high),
            partialFillIndex: firstTouch?.index ?? null,
            filledIndex: firstTouch?.index ?? null,
            status: firstTouch ? 'filled' : 'active',
            evidence: ['连续 20 根 K 线未触及 EMA20'],
            warnings: firstTouch ? ['首次触及只表示长趋势回调，不自动构成反转'] : [],
            provenance: 'book-concept',
          })
        }
      } else {
        untouchedRunStart = null
        untouchedRunLength = 0
      }
    })

    return gaps.sort(
      (left, right) =>
        left.knownAtIndex - right.knownAtIndex ||
        left.startIndex - right.startIndex ||
        left.kind.localeCompare(right.kind),
    )
  }

  function findRawSwings(bars) {
    const points = []
    for (let index = SWING_WINDOW_SIZE; index < bars.length - SWING_WINDOW_SIZE; index += 1) {
      const current = bars[index]
      let isHigh = true
      let isLow = true
      for (let offset = 1; offset <= SWING_WINDOW_SIZE; offset += 1) {
        if (current.high <= bars[index - offset].high || current.high < bars[index + offset].high) {
          isHigh = false
        }
        if (current.low >= bars[index - offset].low || current.low > bars[index + offset].low) {
          isLow = false
        }
      }
      if (isHigh) {
        points.push({ index, confirmedIndex: index + SWING_WINDOW_SIZE, price: current.high, type: 'high' })
      }
      if (isLow) {
        points.push({ index, confirmedIndex: index + SWING_WINDOW_SIZE, price: current.low, type: 'low' })
      }
    }
    return points.sort((left, right) => left.index - right.index || (left.type === 'low' ? -1 : 1))
  }

  function swingHierarchyScore(point, bars, previousOpposite) {
    const incomingBars = bars.slice(Math.max(0, point.index - SWING_WINDOW_SIZE), point.index + 1)
    const confirmationBars = bars.slice(point.index + 1, point.confirmedIndex + 1)
    const fallbackIncomingMove =
      point.type === 'high'
        ? point.price - Math.min(...incomingBars.map((bar) => bar.low))
        : Math.max(...incomingBars.map((bar) => bar.high)) - point.price
    const incomingMove = previousOpposite
      ? Math.abs(point.price - previousOpposite.price)
      : fallbackIncomingMove
    const reversalMove =
      point.type === 'high'
        ? point.price - Math.min(...confirmationBars.map((bar) => bar.low))
        : Math.max(...confirmationBars.map((bar) => bar.high)) - point.price
    const atrAtConfirmation = calculateAtr(bars.slice(0, point.confirmedIndex + 1))
    const normalizedAtr = Math.max(atrAtConfirmation, DEFAULT_TICK_SIZE)
    const balancedMove = Math.min(incomingMove, reversalMove) / normalizedAtr
    const normalizedIncomingMove = incomingMove / normalizedAtr
    const duration = previousOpposite ? Math.max(0, point.index - previousOpposite.index) : SWING_WINDOW_SIZE
    const pivotRange = bars[point.index].high - bars[point.index].low
    const recentMedianRange = median(
      bars.slice(Math.max(0, point.index - 20), point.index + 1).map((bar) => bar.high - bar.low),
    )
    const rangeExpansion = recentMedianRange > 0 ? pivotRange / recentMedianRange : 1
    return clamp01(
      Math.min(balancedMove / MAJOR_SWING_BALANCED_MOVE_ATR, 1) * 0.5 +
        Math.min(normalizedIncomingMove / MAJOR_SWING_INCOMING_LEG_ATR, 1) * 0.2 +
        Math.min(duration / MAJOR_SWING_DURATION_BARS, 1) * 0.2 +
        Math.min(rangeExpansion / MAJOR_SWING_RANGE_EXPANSION, 1) * 0.1,
    )
  }

  function labelSwings(points, bars) {
    let previousHigh = null
    let previousLow = null
    return points.map((point) => {
      let label
      if (point.type === 'high') {
        if (previousHigh === null) {
          label = 'H'
        } else {
          const comparison = comparePrices(point.price, previousHigh.price)
          label = comparison > 0 ? 'HH' : comparison < 0 ? 'LH' : 'EH'
        }
      } else {
        if (previousLow === null) {
          label = 'L'
        } else {
          const comparison = comparePrices(point.price, previousLow.price)
          label = comparison > 0 ? 'HL' : comparison < 0 ? 'LL' : 'EL'
        }
      }
      const previousOpposite = point.type === 'high' ? previousLow : previousHigh
      const score = swingHierarchyScore(point, bars, previousOpposite)
      if (point.type === 'high') {
        previousHigh = point
      } else {
        previousLow = point
      }
      return {
        ...point,
        pivotIndex: point.index,
        knownAtIndex: point.confirmedIndex,
        label,
        level: score >= MAJOR_SWING_SCORE ? 'major' : 'minor',
        score,
        provenance: 'implementation-proxy',
      }
    })
  }

  function moreExtremeSwing(next, current) {
    if (next.type !== current.type) {
      return false
    }
    const comparison = comparePrices(next.price, current.price)
    return next.type === 'high' ? comparison > 0 : comparison < 0
  }

  function alternatingLegAnchors(swings) {
    const anchors = []
    let pending = null
    swings
      .slice()
      .sort((left, right) => left.index - right.index || left.confirmedIndex - right.confirmedIndex)
      .forEach((point) => {
        if (!pending) {
          pending = point
          return
        }
        if (point.type === pending.type) {
          if (moreExtremeSwing(point, pending)) {
            pending = point
          }
          return
        }
        anchors.push(pending)
        pending = point
      })
    if (pending) {
      anchors.push(pending)
    }
    return anchors
  }

  function buildPriceLegs(bars, swings) {
    const anchors = alternatingLegAnchors(swings)
    const legs = anchors
      .slice(1)
      .map((end, index) => {
        const start = anchors[index]
        if (end.index <= start.index || comparePrices(end.price, start.price) === 0) {
          return null
        }
        const direction = end.price > start.price ? 'bull' : 'bear'
        const magnitude = Math.abs(end.price - start.price)
        const atrAtConfirmation = calculateAtr(bars.slice(0, end.confirmedIndex + 1))
        const level =
          (start.level === 'major' && end.level === 'major') ||
          magnitude >= Math.max(atrAtConfirmation, DEFAULT_TICK_SIZE) * MAJOR_PRICE_LEG_MAGNITUDE_ATR
            ? 'major'
            : 'minor'
        return {
          id: `leg:${start.index}:${end.index}:${direction}`,
          direction,
          startIndex: start.index,
          endIndex: end.index,
          startPrice: start.price,
          endPrice: end.price,
          bars: end.index - start.index,
          magnitude,
          confirmedAtIndex: end.confirmedIndex,
          knownAtIndex: end.confirmedIndex,
          level,
          retracementAfter: null,
          retracementKnownAtIndex: null,
          provenance: 'implementation-proxy',
        }
      })
      .filter((leg) => leg !== null)

    return legs.map((leg, index) => {
      const retracementLeg = legs[index + 1]
      if (!retracementLeg || retracementLeg.direction === leg.direction || leg.magnitude <= 0) {
        return leg
      }
      return {
        ...leg,
        retracementAfter: retracementLeg.magnitude / leg.magnitude,
        retracementKnownAtIndex: retracementLeg.confirmedAtIndex,
      }
    })
  }

  function barCountFollowThrough(bars, signalBarIndex, direction, triggerPrice, invalidationPrice) {
    const limit = Math.min(bars.length - 1, signalBarIndex + SIGNAL_CONFIRMATION_WINDOW)
    let entryTriggeredIndex = null
    for (let index = signalBarIndex + 1; index <= limit; index += 1) {
      const current = bars[index]
      const invalidated =
        direction === 'bullish'
          ? current.low < invalidationPrice - comparisonTolerance(current.low, invalidationPrice)
          : current.high > invalidationPrice + comparisonTolerance(current.high, invalidationPrice)
      if (invalidated && entryTriggeredIndex === null) {
        return { entryTriggeredIndex: null, followThrough: 'failed', statusKnownAtIndex: index }
      }
      const triggered = direction === 'bullish' ? current.high > triggerPrice : current.low < triggerPrice
      if (entryTriggeredIndex === null && triggered) {
        entryTriggeredIndex = index
        continue
      }
      if (entryTriggeredIndex !== null) {
        const signal = bars[signalBarIndex]
        const strong =
          direction === 'bullish'
            ? current.close > signal.high && current.close > current.open
            : current.close < signal.low && current.close < current.open
        if (strong) {
          return { entryTriggeredIndex, followThrough: 'strong', statusKnownAtIndex: index }
        }
      }
    }
    return {
      entryTriggeredIndex,
      followThrough: entryTriggeredIndex === null ? 'none' : 'weak',
      statusKnownAtIndex: entryTriggeredIndex ?? limit,
    }
  }

  /**
   * Counts recovery attempts inside a confirmed trend pullback. The trend at
   * every event is derived only from swings that were confirmed beforehand.
   */
  function findBarCountEvents(bars, swings) {
    const events = []
    let state = null
    for (let index = 1; index < bars.length; index += 1) {
      const knownSwings = prioritizedStructuralSwings(swings.filter((point) => point.knownAtIndex < index))
      const localTrend = determineTrend(knownSwings).trend
      if (localTrend === 'range') {
        state = null
        continue
      }
      if (!state || state.trend !== localTrend) {
        state = {
          trend: localTrend,
          pullbackStartIndex: null,
          pullbackOriginPrice: localTrend === 'up' ? bars[index - 1].high : bars[index - 1].low,
          pullbackExtreme: localTrend === 'up' ? bars[index - 1].low : bars[index - 1].high,
          count: 0,
          canCountAttempt: false,
        }
      }

      const current = bars[index]
      const previous = bars[index - 1]
      const adverseMove =
        localTrend === 'up'
          ? current.low < previous.low - comparisonTolerance(current.low, previous.low)
          : current.high > previous.high + comparisonTolerance(current.high, previous.high)
      if (state.pullbackStartIndex === null && adverseMove) {
        state.pullbackStartIndex = index
        state.pullbackOriginPrice =
          localTrend === 'up'
            ? Math.max(...bars.slice(Math.max(0, index - 3), index).map((bar) => bar.high))
            : Math.min(...bars.slice(Math.max(0, index - 3), index).map((bar) => bar.low))
        state.pullbackExtreme = localTrend === 'up' ? current.low : current.high
        state.count = 0
        state.canCountAttempt = true
      } else if (state.pullbackStartIndex !== null && adverseMove) {
        const extended =
          localTrend === 'up'
            ? current.low < state.pullbackExtreme - comparisonTolerance(current.low, state.pullbackExtreme)
            : current.high > state.pullbackExtreme + comparisonTolerance(current.high, state.pullbackExtreme)
        if (extended) {
          state.pullbackExtreme =
            localTrend === 'up'
              ? Math.min(state.pullbackExtreme, current.low)
              : Math.max(state.pullbackExtreme, current.high)
          state.canCountAttempt = true
        }
      }

      if (state.pullbackStartIndex === null) {
        continue
      }
      const resumed =
        localTrend === 'up'
          ? current.close >
            state.pullbackOriginPrice + comparisonTolerance(current.close, state.pullbackOriginPrice)
          : current.close <
            state.pullbackOriginPrice - comparisonTolerance(current.close, state.pullbackOriginPrice)
      const recoveryAttempt =
        localTrend === 'up'
          ? current.high > previous.high + comparisonTolerance(current.high, previous.high)
          : current.low < previous.low - comparisonTolerance(current.low, previous.low)
      if (state.canCountAttempt && recoveryAttempt) {
        state.count = Math.min(4, state.count + 1)
        state.canCountAttempt = false
        const direction = localTrend === 'up' ? 'bullish' : 'bearish'
        const label = `${localTrend === 'up' ? 'H' : 'L'}${state.count}`
        const triggerPrice =
          localTrend === 'up' ? current.high + DEFAULT_TICK_SIZE : current.low - DEFAULT_TICK_SIZE
        const invalidationPrice = state.pullbackExtreme
        const lifecycle = barCountFollowThrough(bars, index, direction, triggerPrice, invalidationPrice)
        events.push({
          id: `bar-count:${label}:${index}`,
          label,
          direction,
          index,
          signalBarIndex: index,
          pullbackStartIndex: state.pullbackStartIndex,
          entryTriggerPrice: triggerPrice,
          entryTriggeredIndex: lifecycle.entryTriggeredIndex,
          followThrough: lifecycle.followThrough,
          invalidationPrice,
          knownAtIndex: index,
          statusKnownAtIndex: lifecycle.statusKnownAtIndex,
          evidence: [
            `${localTrend === 'up' ? '多头' : '空头'}结构中的逆向回调`,
            `第 ${state.count} 次顺势恢复尝试`,
          ],
          warnings: state.count >= 3 ? ['计数已复杂化，结构更接近交易区间'] : [],
          provenance: 'implementation-proxy',
        })
      }
      if (resumed) {
        state.pullbackStartIndex = null
        state.count = 0
        state.canCountAttempt = false
      }
    }
    return events
  }

  function scorePoints(points) {
    let score = 0
    points.slice(1).forEach((point, index) => {
      score += comparePrices(point.price, points[index].price)
    })
    return score
  }

  function determineTrend(swings) {
    const recentHighs = swings.filter((point) => point.type === 'high').slice(-4)
    const recentLows = swings.filter((point) => point.type === 'low').slice(-4)
    const highScore = scorePoints(recentHighs)
    const lowScore = scorePoints(recentLows)
    const score = highScore + lowScore
    if (score >= 3 && highScore > 0 && lowScore > 0) {
      return {
        trend: 'up',
        score,
        highScore,
        lowScore,
        description: '高点与低点整体同步上移，当前为多头价格结构。',
      }
    }
    if (score <= -3 && highScore < 0 && lowScore < 0) {
      return {
        trend: 'down',
        score,
        highScore,
        lowScore,
        description: '高点与低点整体同步下移，当前为空头价格结构。',
      }
    }
    return {
      trend: 'range',
      score,
      highScore,
      lowScore,
      description: '高低点尚未形成一致方向，当前更接近震荡或转换结构。',
    }
  }

  function marketStateLabel(state) {
    const labels = {
      'strong-bull-trend': '强牛趋势',
      'bull-channel': '牛趋势通道',
      'broad-bull-channel': '宽幅牛通道',
      'strong-bear-trend': '强熊趋势',
      'bear-channel': '熊趋势通道',
      'broad-bear-channel': '宽幅熊通道',
      'trading-range': '交易区间',
      'tight-trading-range': '紧密交易区间',
      'breakout-mode': '突破模式',
      transition: '转换阶段',
    }
    return labels[state]
  }

  function classifyMarketState(
    bars,
    trend,
    legs,
    classifications,
    ema20,
    tradingRanges = [],
    structureStartIndex = 0,
    directionContext = null,
  ) {
    trend = directionContext?.trend ?? trend
    const recentBars = bars.slice(-20)
    const recentClassifications = classifications.slice(-20)
    const knownAtIndex = Math.max(0, bars.length - 1)
    if (recentBars.length === 0) {
      return {
        state: 'transition',
        label: marketStateLabel('transition'),
        score: 0,
        knownAtIndex,
        evidence: [],
        warnings: ['数据不足'],
        provenance: 'implementation-proxy',
      }
    }
    const atr = Math.max(calculateAtr(bars), DEFAULT_TICK_SIZE)
    const averageOverlap = average(recentClassifications.map((item) => item.features.overlapRatio))
    const recentRange =
      Math.max(...recentBars.map((bar) => bar.high)) - Math.min(...recentBars.map((bar) => bar.low))
    const direction = trend === 'up' ? 'bullish' : trend === 'down' ? 'bearish' : null
    const directionalTrendBars = direction
      ? recentClassifications.filter((item) => item.isTrendBar && item.direction === direction).length
      : 0
    const directionalRatio = directionalTrendBars / Math.max(1, recentClassifications.length)
    const emaPoints = ema20.points.filter((point) => point.index >= Math.max(0, bars.length - 10))
    const emaSlope =
      emaPoints.length >= 2
        ? (emaPoints[emaPoints.length - 1].value - emaPoints[0].value) /
          (Math.max(1, emaPoints.length - 1) * atr)
        : 0
    const matchingLeg = direction
      ? legs
          .slice()
          .reverse()
          .find(
            (leg) =>
              leg.startIndex >= structureStartIndex &&
              leg.direction === (direction === 'bullish' ? 'bull' : 'bear'),
          )
      : undefined
    const retracement = matchingLeg?.retracementAfter ?? 0
    let state
    const evidence = []
    const warnings = []
    let score = 0.5

    const activeRange = selectCurrentTradingRange(tradingRanges, bars, structureStartIndex)
    if (activeRange && !directionContext?.direction) {
      if (activeRange.kind === 'tight-trading-range') {
        state = 'tight-trading-range'
      } else if (activeRange.status === 'breakout-mode' || activeRange.kind === 'triangle') {
        state = 'breakout-mode'
      } else {
        state = 'trading-range'
      }
      evidence.push(...activeRange.evidence)
      warnings.push(...activeRange.warnings)
      score = activeRange.score
    } else if (trend === 'range') {
      const tight = recentRange <= atr * 4 && averageOverlap >= 0.65
      state = tight ? 'tight-trading-range' : 'transition'
      if (tight) {
        evidence.push('近期高度较小且 K 线重叠显著')
        score = clamp01(averageOverlap)
      } else {
        evidence.push(directionContext ? '方向依据与近期价格尚未形成一致方向' : '高低摆动尚未形成一致方向')
        warnings.push('真实交易区间仍需成组边界测试确认')
      }
    } else {
      const strong =
        directionalRatio >= 0.3 &&
        averageOverlap <= 0.5 &&
        (trend === 'up' ? emaSlope >= 0.08 : emaSlope <= -0.08) &&
        retracement <= 0.45
      const broad = retracement >= 0.65 || averageOverlap >= 0.62
      if (strong) {
        state = trend === 'up' ? 'strong-bull-trend' : 'strong-bear-trend'
        evidence.push('同向趋势 K 线占比较高', '相邻重叠较低', 'EMA20 具有同向斜率')
        score = clamp01(0.55 + directionalRatio + Math.abs(emaSlope))
      } else if (broad) {
        state = trend === 'up' ? 'broad-bull-channel' : 'broad-bear-channel'
        evidence.push('仍有净方向，但回调较深或双向重叠增加')
        warnings.push('追随突破的质量低于强趋势环境')
        score = clamp01(0.55 + Math.max(retracement, averageOverlap) * 0.35)
      } else {
        state = trend === 'up' ? 'bull-channel' : 'bear-channel'
        evidence.push(
          directionContext?.source === 'breakout'
            ? '已确认突破保持方向，当前包含回调'
            : '摆动保持净方向并包含规律回调',
        )
        score = clamp01(0.55 + Math.abs(emaSlope) * 0.5)
      }
    }
    const consolidation = Boolean(activeRange && directionContext?.direction)
    if (directionContext) {
      evidence.push(...directionContext.evidence)
      warnings.push(...directionContext.warnings)
    }
    if (consolidation) evidence.push('近期为趋势内整理，已建立的方向依据仍有效')
    return {
      state,
      label: consolidation ? (trend === 'up' ? '上升趋势内整理' : '下降趋势内整理') : marketStateLabel(state),
      score,
      knownAtIndex,
      evidence,
      warnings,
      provenance: 'implementation-proxy',
    }
  }

  function determineAlwaysIn(directionContext) {
    return summarizeDirection(directionContext)
  }

  function prioritizedStructuralSwings(swings) {
    const majorSwings = swings.filter((point) => point.level === 'major')
    const hasMajorSkeleton =
      majorSwings.filter((point) => point.type === 'high').length >= 2 &&
      majorSwings.filter((point) => point.type === 'low').length >= 2
    return hasMajorSkeleton ? majorSwings : swings
  }

  function alternatingSwingLegs(points) {
    const ordered = points.slice().sort((left, right) => left.index - right.index)
    let legs = 0
    for (let index = 1; index < ordered.length; index += 1) {
      if (ordered[index].type !== ordered[index - 1].type) {
        legs += 1
      }
    }
    return legs
  }

  function rangeBoundaryAt(range, index, side) {
    const startPrice = side === 'upper' ? range.upperStartPrice : range.lowerStartPrice
    const endPrice = side === 'upper' ? range.upperEndPrice : range.lowerEndPrice
    const span = Math.max(1, range.knownAtIndex - range.startIndex)
    return startPrice + ((endPrice - startPrice) / span) * (index - range.startIndex)
  }

  function applyTradingRangeBreakout(bars, range, atr) {
    const buffer = Math.max(DEFAULT_TICK_SIZE, atr * BREAKOUT_ATR_BUFFER_RATIO)
    for (let index = range.knownAtIndex + 1; index < bars.length; index += 1) {
      const upper = rangeBoundaryAt(range, index, 'upper')
      const lower = rangeBoundaryAt(range, index, 'lower')
      const direction =
        bars[index].close > upper + buffer ? 'bullish' : bars[index].close < lower - buffer ? 'bearish' : null
      if (!direction) {
        continue
      }
      const followThroughEnd = Math.min(bars.length - 1, index + 2)
      for (let followIndex = index + 1; followIndex <= followThroughEnd; followIndex += 1) {
        const followUpper = rangeBoundaryAt(range, followIndex, 'upper')
        const followLower = rangeBoundaryAt(range, followIndex, 'lower')
        const returnedInside =
          bars[followIndex].close <= followUpper && bars[followIndex].close >= followLower
        if (returnedInside) {
          return {
            ...range,
            status: 'failed-breakout',
            endIndex: followIndex,
            statusKnownAtIndex: followIndex,
            breakoutIndex: index,
            breakoutDirection: direction,
            failedBreakoutIndex: followIndex,
            warnings: [...range.warnings, '边界突破缺乏跟进并重新进入原区间'],
          }
        }
        const followed =
          direction === 'bullish'
            ? bars[followIndex].close > followUpper + buffer
            : bars[followIndex].close < followLower - buffer
        if (followed) {
          return {
            ...range,
            status: 'broken',
            endIndex: followIndex,
            statusKnownAtIndex: followIndex,
            breakoutIndex: index,
            breakoutDirection: direction,
            failedBreakoutIndex: null,
            evidence: [...range.evidence, '边界突破获得同向收盘跟进'],
          }
        }
      }
      return {
        ...range,
        status: 'breakout-mode',
        endIndex: bars.length - 1,
        statusKnownAtIndex: index,
        breakoutIndex: index,
        breakoutDirection: direction,
        warnings: [...range.warnings, '边界已越过，仍在等待跟进确认'],
      }
    }
    return { ...range, endIndex: bars.length - 1 }
  }

  function buildSwingRangeCandidate(bars, points, classifications) {
    const highs = points.filter((point) => point.type === 'high')
    const lows = points.filter((point) => point.type === 'low')
    if (highs.length < 2 || lows.length < 2 || alternatingSwingLegs(points) < 3) {
      return null
    }
    const knownAtIndex = Math.max(...points.map((point) => point.knownAtIndex))
    if (knownAtIndex >= bars.length) {
      return null
    }
    const startIndex = Math.min(...points.map((point) => point.index))
    const atr = Math.max(calculateAtr(bars.slice(0, knownAtIndex + 1)), DEFAULT_TICK_SIZE)
    const centerPrice = average(points.map((point) => point.price))
    const tolerance = Math.max(atr * 0.65, Math.abs(centerPrice) * 0.0035)
    const orderedHighs = highs.slice().sort((left, right) => left.index - right.index)
    const orderedLows = lows.slice().sort((left, right) => left.index - right.index)
    const convergingHighs =
      orderedHighs.length >= 3 &&
      orderedHighs
        .slice(1)
        .every((point, index) => comparePrices(point.price, orderedHighs[index].price) <= 0)
    const convergingLows =
      orderedLows.length >= 3 &&
      orderedLows.slice(1).every((point, index) => comparePrices(point.price, orderedLows[index].price) >= 0)
    const triangle =
      convergingHighs &&
      convergingLows &&
      orderedHighs[orderedHighs.length - 1].price > orderedLows[orderedLows.length - 1].price
    const horizontal =
      Math.max(...highs.map((point) => point.price)) - Math.min(...highs.map((point) => point.price)) <=
        tolerance * 2 &&
      Math.max(...lows.map((point) => point.price)) - Math.min(...lows.map((point) => point.price)) <=
        tolerance * 2
    if (!triangle && !horizontal) {
      return null
    }
    const upperStartPrice = triangle ? orderedHighs[0].price : average(highs.map((point) => point.price))
    const upperEndPrice = triangle ? orderedHighs[orderedHighs.length - 1].price : upperStartPrice
    const lowerStartPrice = triangle ? orderedLows[0].price : average(lows.map((point) => point.price))
    const lowerEndPrice = triangle ? orderedLows[orderedLows.length - 1].price : lowerStartPrice
    const high = Math.max(upperStartPrice, upperEndPrice)
    const low = Math.min(lowerStartPrice, lowerEndPrice)
    if (high - low < atr * 1.5) {
      return null
    }
    const overlap = average(
      classifications.slice(startIndex, knownAtIndex + 1).map((item) => item.features.overlapRatio),
    )
    const alternatingLegs = alternatingSwingLegs(points)
    const kind = triangle ? 'triangle' : 'trading-range'
    const base = {
      id: `range:${kind}:${startIndex}:${knownAtIndex}`,
      kind,
      label: triangle ? 'Triangle' : 'Trading Range',
      status: triangle ? 'breakout-mode' : 'confirmed',
      startIndex,
      endIndex: bars.length - 1,
      knownAtIndex,
      statusKnownAtIndex: knownAtIndex,
      high,
      low,
      mid: (high + low) / 2,
      upperTouches: highs.map((point) => point.index),
      lowerTouches: lows.map((point) => point.index),
      alternatingLegs,
      breakoutIndex: null,
      breakoutDirection: null,
      failedBreakoutIndex: null,
      upperStartPrice,
      upperEndPrice,
      lowerStartPrice,
      lowerEndPrice,
      score: clamp01(0.35 + points.length * 0.06 + alternatingLegs * 0.04 + overlap * 0.2),
      evidence: [
        `${highs.length} 次上边界测试与 ${lows.length} 次下边界测试`,
        `${alternatingLegs} 条交替价格腿`,
        triangle ? '摆动高点降低且摆动低点抬高' : '成组测试形成近似水平边界',
      ],
      warnings: triangle ? ['接近收敛顶点时假突破概率上升'] : [],
      provenance: 'implementation-proxy',
    }
    return applyTradingRangeBreakout(bars, base, atr)
  }

  function buildTightRangeCandidate(bars, classifications) {
    const length = 12
    if (bars.length < length) return null
    const startIndex = bars.length - length
    const recentBars = bars.slice(startIndex)
    const high = Math.max(...recentBars.map((bar) => bar.high))
    const low = Math.min(...recentBars.map((bar) => bar.low))
    const atr = Math.max(calculateAtr(bars), DEFAULT_TICK_SIZE)
    const overlap = average(classifications.slice(startIndex).map((item) => item.features.overlapRatio))
    if (high - low > atr * 4 || overlap < 0.7) {
      return null
    }
    return {
      id: `range:tight:${startIndex}:${bars.length - 1}`,
      kind: 'tight-trading-range',
      label: 'Tight Trading Range',
      status: 'breakout-mode',
      startIndex,
      endIndex: bars.length - 1,
      knownAtIndex: bars.length - 1,
      statusKnownAtIndex: bars.length - 1,
      high,
      low,
      mid: (high + low) / 2,
      upperTouches: [],
      lowerTouches: [],
      alternatingLegs: 0,
      breakoutIndex: null,
      breakoutDirection: null,
      failedBreakoutIndex: null,
      upperStartPrice: high,
      upperEndPrice: high,
      lowerStartPrice: low,
      lowerEndPrice: low,
      score: clamp01(0.55 + overlap * 0.35),
      evidence: ['最近 12 根 K 线高度受限且重叠率很高'],
      warnings: ['紧密区间中多空概率接近，等待强突破和跟进'],
      provenance: 'implementation-proxy',
    }
  }

  /** Builds ranges from confirmed alternating swings instead of a rolling high/low shortcut. */
  function findTradingRangeCandidates(bars, swings, classifications) {
    const candidates = []
    const ordered = swings.slice().sort((left, right) => left.index - right.index)
    for (let end = 3; end < ordered.length; end += 1) {
      const earliest = Math.max(0, end - 8)
      for (let start = earliest; start <= end - 3; start += 1) {
        const candidate = buildSwingRangeCandidate(bars, ordered.slice(start, end + 1), classifications)
        if (candidate) candidates.push(candidate)
      }
    }
    const tight = buildTightRangeCandidate(bars, classifications)
    if (tight) candidates.push(tight)
    return candidates
  }

  function findTradingRanges(
    bars,
    swings,
    classifications,
    candidates = findTradingRangeCandidates(bars, swings, classifications),
  ) {
    const sorted = candidates
      .slice()
      .sort(
        (left, right) =>
          right.score - left.score ||
          right.knownAtIndex - left.knownAtIndex ||
          right.startIndex - left.startIndex,
      )
    const distinct = sorted.filter(
      (range, index) =>
        !sorted
          .slice(0, index)
          .some(
            (other) =>
              range.kind === other.kind &&
              Math.abs(range.high - other.high) <= comparisonTolerance(range.high, other.high) * 3 &&
              Math.abs(range.low - other.low) <= comparisonTolerance(range.low, other.low) * 3,
          ),
    )
    return distinct.slice(0, 4).sort((left, right) => left.knownAtIndex - right.knownAtIndex)
  }

  function measuredMoveLifecycle(bars, knownAtIndex, projectionPrice, distance, targetPrice, direction) {
    for (let index = knownAtIndex; index < bars.length; index += 1) {
      const reached = direction === 'up' ? bars[index].high >= targetPrice : bars[index].low <= targetPrice
      if (reached) {
        return { status: 'reached', statusKnownAtIndex: index, reachedIndex: index, invalidatedIndex: null }
      }
      const invalidated =
        direction === 'up'
          ? bars[index].low < projectionPrice - distance * 0.5
          : bars[index].high > projectionPrice + distance * 0.5
      if (invalidated) {
        return {
          status: 'invalidated',
          statusKnownAtIndex: index,
          reachedIndex: null,
          invalidatedIndex: index,
        }
      }
    }
    return { status: 'active', statusKnownAtIndex: knownAtIndex, reachedIndex: null, invalidatedIndex: null }
  }

  function createMeasuredMove(
    bars,
    basis,
    direction,
    basisStartIndex,
    basisStartPrice,
    basisEndIndex,
    basisEndPrice,
    projectionIndex,
    projectionPrice,
    knownAtIndex,
    reason,
  ) {
    const distance = Math.abs(basisEndPrice - basisStartPrice)
    if (distance <= DEFAULT_TICK_SIZE) return null
    const targetPrice = direction === 'up' ? projectionPrice + distance : projectionPrice - distance
    const lifecycle = measuredMoveLifecycle(
      bars,
      knownAtIndex,
      projectionPrice,
      distance,
      targetPrice,
      direction,
    )
    return {
      id: `measured:${basis}:${basisStartIndex}:${basisEndIndex}:${projectionIndex}:${direction}`,
      basis,
      direction,
      basisStartIndex,
      basisStartPrice,
      basisEndIndex,
      basisEndPrice,
      projectionIndex,
      projectionPrice,
      distance,
      targetPrice,
      knownAtIndex,
      ...lifecycle,
      evidence: [reason, `等距投影 ${distance.toFixed(3)}`],
      warnings: ['测量目标是潜在磁吸位，不保证价格必然到达'],
      provenance: 'book-concept',
    }
  }

  function buildMeasuredMoves(bars, legs, tradingRanges, gaps, reversalPatterns = []) {
    const moves = []
    for (let index = 0; index < legs.length - 1; index += 1) {
      const basisLeg = legs[index]
      const retracement = legs[index + 1]
      if (basisLeg.direction === retracement.direction || retracement.endIndex <= basisLeg.endIndex) continue
      const direction = basisLeg.direction === 'bull' ? 'up' : 'down'
      const move = createMeasuredMove(
        bars,
        'leg',
        direction,
        basisLeg.startIndex,
        basisLeg.startPrice,
        basisLeg.endIndex,
        basisLeg.endPrice,
        retracement.endIndex,
        retracement.endPrice,
        retracement.knownAtIndex,
        '以前一条推进腿为基准，从回调终点投影',
      )
      if (move) moves.push(move)
    }
    tradingRanges
      .filter((range) => range.breakoutIndex !== null && range.breakoutDirection !== null)
      .forEach((range) => {
        const bullish = range.breakoutDirection === 'bullish'
        const move = createMeasuredMove(
          bars,
          'range',
          bullish ? 'up' : 'down',
          range.startIndex,
          bullish ? range.low : range.high,
          range.startIndex,
          bullish ? range.high : range.low,
          range.breakoutIndex,
          bullish ? range.high : range.low,
          range.statusKnownAtIndex,
          '以已突破交易区间的完整高度为基准投影',
        )
        if (move) moves.push(move)
      })
    gaps
      .filter((gap) => gap.kind === 'measuring-gap')
      .forEach((gap) => {
        const bullish = gap.direction === 'bullish'
        const move = createMeasuredMove(
          bars,
          'gap',
          bullish ? 'up' : 'down',
          gap.startIndex,
          bullish ? gap.lowerPrice : gap.upperPrice,
          gap.startIndex,
          bullish ? gap.upperPrice : gap.lowerPrice,
          gap.startIndex,
          bullish ? gap.upperPrice : gap.lowerPrice,
          gap.knownAtIndex,
          '以持续未回补的测量缺口宽度为基准',
        )
        if (move) moves.push(move)
      })
    reversalPatterns
      .filter(
        (pattern) =>
          (pattern.kind === 'double-top' || pattern.kind === 'double-bottom') &&
          pattern.status === 'confirmed' &&
          pattern.triggerIndex !== null &&
          pattern.triggerPrice !== null,
      )
      .forEach((pattern) => {
        const extreme = pattern.anchors.find((anchor) => anchor.role === 'second-test')
        const neckline = pattern.anchors.find((anchor) => anchor.role === 'neckline')
        if (!extreme || !neckline || pattern.triggerIndex === null) return
        const move = createMeasuredMove(
          bars,
          'pattern',
          pattern.direction === 'bullish' ? 'up' : 'down',
          neckline.index,
          neckline.price,
          extreme.index,
          extreme.price,
          pattern.triggerIndex,
          neckline.price,
          pattern.statusKnownAtIndex,
          `以 ${pattern.label} 的颈线到极值高度为基准`,
        )
        if (move) moves.push(move)
      })
    const distinct = moves.filter(
      (move, index) =>
        !moves
          .slice(0, index)
          .some(
            (other) =>
              move.basis === other.basis &&
              move.direction === other.direction &&
              Math.abs(move.targetPrice - other.targetPrice) <=
                comparisonTolerance(move.targetPrice, other.targetPrice) * 3,
          ),
    )
    return distinct.sort((left, right) => left.knownAtIndex - right.knownAtIndex).slice(-12)
  }

  function magnetSourceWeight(kind) {
    if (kind === 'trading-range-high' || kind === 'trading-range-low') return 3
    if (kind === 'breakout-point' || kind === 'measured-move-target') return 2.5
    if (kind === 'swing-high' || kind === 'swing-low') return 2
    if (kind === 'trading-range-mid' || kind === 'ema20') return 1.5
    return 1
  }

  function buildMagnetLevels(bars, swings, tradingRanges, breakouts, ema20, measuredMoves) {
    if (bars.length === 0) return []
    const currentPrice = bars[bars.length - 1].close
    const sources = []
    swings
      .filter((point) => point.level === 'major')
      .slice(-8)
      .forEach((point) =>
        sources.push({
          kind: point.type === 'high' ? 'swing-high' : 'swing-low',
          label: point.label,
          price: point.price,
          knownAtIndex: point.knownAtIndex,
          objectId: `swing-${point.type}:${point.index}`,
        }),
      )
    tradingRanges.slice(-3).forEach((range) => {
      sources.push(
        {
          kind: 'trading-range-high',
          label: '区间上沿',
          price: range.high,
          knownAtIndex: range.knownAtIndex,
          objectId: `${range.id}:high`,
        },
        {
          kind: 'trading-range-low',
          label: '区间下沿',
          price: range.low,
          knownAtIndex: range.knownAtIndex,
          objectId: `${range.id}:low`,
        },
        {
          kind: 'trading-range-mid',
          label: '区间中点',
          price: range.mid,
          knownAtIndex: range.knownAtIndex,
          objectId: `${range.id}:mid`,
        },
      )
    })
    breakouts.slice(-5).forEach((breakout) =>
      sources.push({
        kind: 'breakout-point',
        label: '突破点',
        price: breakout.boundaryPrice,
        knownAtIndex: breakout.knownAtIndex,
        objectId: breakout.id,
      }),
    )
    const latestEma = ema20.points[ema20.points.length - 1]
    if (latestEma)
      sources.push({
        kind: 'ema20',
        label: 'EMA20',
        price: latestEma.value,
        knownAtIndex: latestEma.knownAtIndex,
        objectId: `ema20:${latestEma.index}`,
      })
    measuredMoves
      .filter((move) => move.status === 'active')
      .forEach((move) =>
        sources.push({
          kind: 'measured-move-target',
          label: '测量目标',
          price: move.targetPrice,
          knownAtIndex: move.knownAtIndex,
          objectId: move.id,
        }),
      )
    const roundStep = Math.abs(currentPrice) >= 100 ? 10 : Math.abs(currentPrice) >= 10 ? 1 : 0.1
    const rounded = Math.round(currentPrice / roundStep) * roundStep
    for (const price of [rounded - roundStep, rounded, rounded + roundStep]) {
      if (price > 0)
        sources.push({
          kind: 'round-number',
          label: '整数位',
          price,
          knownAtIndex: bars.length - 1,
          objectId: `round:${price}`,
        })
    }
    const tolerance = Math.max(calculateAtr(bars) * 0.25, Math.abs(currentPrice) * 0.0015, DEFAULT_TICK_SIZE)
    const clusters = []
    sources
      .slice()
      .sort((left, right) => left.price - right.price)
      .forEach((source) => {
        const matched = clusters.find(
          (cluster) => Math.abs(average(cluster.map((item) => item.price)) - source.price) <= tolerance,
        )
        if (matched) matched.push(source)
        else clusters.push([source])
      })
    const magnets = clusters
      .map((cluster) => {
        const price = average(cluster.map((source) => source.price))
        const score =
          cluster.reduce((total, source) => total + magnetSourceWeight(source.kind), 0) -
          (Math.abs(price - currentPrice) / Math.max(calculateAtr(bars), DEFAULT_TICK_SIZE)) * 0.15
        return {
          id: `magnet:${cluster
            .map((source) => source.objectId)
            .sort()
            .join('|')}`,
          price,
          zoneLow: Math.min(...cluster.map((source) => source.price)) - tolerance * 0.2,
          zoneHigh: Math.max(...cluster.map((source) => source.price)) + tolerance * 0.2,
          knownAtIndex: Math.max(...cluster.map((source) => source.knownAtIndex)),
          sources: cluster,
          score,
          label: '',
          evidence: cluster.map((source) => `${source.label} ${formatPrice(source.price)}`),
          warnings: cluster.length > 1 ? [] : ['单一来源磁吸位，权重较低'],
          provenance: cluster.every((source) => source.kind !== 'round-number')
            ? 'book-concept'
            : 'product-extension',
        }
      })
      .sort(
        (left, right) =>
          right.score - left.score ||
          Math.abs(left.price - currentPrice) - Math.abs(right.price - currentPrice),
      )
      .slice(0, 8)
    magnets.forEach((magnet, index) => {
      magnet.label = `M${index + 1}${magnet.sources.length > 1 ? '·C' : ''}`
    })
    return magnets
  }

  function spikeAndChannelPatterns(bars, classifications) {
    const patterns = []
    let index = 0
    while (index < classifications.length - 1) {
      const first = classifications[index]
      if (!first.isTrendBar || first.direction === 'neutral') {
        index += 1
        continue
      }
      const direction = first.direction
      let end = index
      while (
        end + 1 < classifications.length &&
        end - index < 3 &&
        classifications[end + 1].isTrendBar &&
        classifications[end + 1].direction === direction
      ) {
        end += 1
      }
      if (end === index) {
        index += 1
        continue
      }
      const atr = Math.max(calculateAtr(bars.slice(0, end + 1)), DEFAULT_TICK_SIZE)
      const move =
        direction === 'bullish' ? bars[end].close - bars[index].open : bars[index].open - bars[end].close
      if (move < atr * 1.5) {
        index = end + 1
        continue
      }
      let pullbackIndex = null
      for (let candidate = end + 1; candidate <= Math.min(bars.length - 1, end + 5); candidate += 1) {
        const adverse =
          direction === 'bullish'
            ? bars[candidate].close < bars[candidate].open || bars[candidate].low < bars[candidate - 1].low
            : bars[candidate].close > bars[candidate].open || bars[candidate].high > bars[candidate - 1].high
        if (adverse) {
          pullbackIndex = candidate
          break
        }
      }
      if (pullbackIndex === null) {
        index = end + 1
        continue
      }
      const channelBars = bars.length - pullbackIndex - 1
      const channelMove =
        direction === 'bullish'
          ? bars[bars.length - 1].close - bars[pullbackIndex].close
          : bars[pullbackIndex].close - bars[bars.length - 1].close
      const active = channelBars >= 3 && channelMove > atr * 0.5
      const failureIndex = bars.findIndex(
        (bar, candidate) =>
          candidate > pullbackIndex &&
          (direction === 'bullish' ? bar.close < bars[index].open : bar.close > bars[index].open),
      )
      const status = failureIndex >= 0 ? 'failed' : active ? 'active' : 'candidate'
      const statusKnownAtIndex = failureIndex >= 0 ? failureIndex : active ? pullbackIndex + 3 : pullbackIndex
      const patternEnd = failureIndex >= 0 ? failureIndex : bars.length - 1
      patterns.push({
        id: `trend-pattern:spike-channel:${index}:${pullbackIndex}:${direction}`,
        kind: 'spike-and-channel',
        label: direction === 'bullish' ? 'Bull Spike & Channel' : 'Bear Spike & Channel',
        direction,
        status,
        startIndex: index,
        endIndex: patternEnd,
        knownAtIndex: pullbackIndex,
        statusKnownAtIndex,
        anchors: [
          { index, price: bars[index].open, role: 'spike-start' },
          { index: end, price: bars[end].close, role: 'spike-end' },
          { index: pullbackIndex, price: bars[pullbackIndex].close, role: 'channel-start' },
          { index: patternEnd, price: bars[patternEnd].close, role: 'channel-end' },
        ],
        score: clamp01(0.45 + Math.min(move / atr, 4) * 0.1 + (active ? 0.15 : 0)),
        evidence: ['短时间连续强趋势 K 线形成 spike', '首次明显回调定义 channel 起点'],
        warnings: active ? [] : ['通道阶段仍缺少足够的顺势推进'],
        provenance: 'implementation-proxy',
      })
      index = end + 1
    }
    return patterns.slice(-4)
  }

  function findTrendPatterns(bars, classifications, legs, marketState, ema20) {
    const patterns = spikeAndChannelPatterns(bars, classifications)
    const bullish = ['strong-bull-trend', 'bull-channel', 'broad-bull-channel'].includes(marketState.state)
    const bearish = ['strong-bear-trend', 'bear-channel', 'broad-bear-channel'].includes(marketState.state)
    const direction = bullish ? 'bullish' : bearish ? 'bearish' : null
    const uniqueLegs = legs.filter(
      (leg, index) => legs.findIndex((candidate) => candidate.id === leg.id) === index,
    )
    if (direction) {
      const advancingDirection = direction === 'bullish' ? 'bull' : 'bear'
      const shallowAdvances = uniqueLegs
        .filter((leg, index) => {
          const retracement = uniqueLegs[index + 1]
          return (
            leg.direction === advancingDirection &&
            leg.retracementAfter !== null &&
            leg.retracementAfter <= 0.45 &&
            retracement &&
            retracement.bars <= 3
          )
        })
        .slice(-3)
      if (shallowAdvances.length >= 2) {
        const start = shallowAdvances[0]
        const end = shallowAdvances[shallowAdvances.length - 1]
        const knownAtIndex = Math.max(
          ...shallowAdvances.map((leg) => leg.retracementKnownAtIndex ?? leg.knownAtIndex),
        )
        const emaByIndex = new Map(ema20.points.map((point) => [point.index, point.value]))
        const sameSideBars = bars.slice(start.startIndex, end.endIndex + 1).filter((bar, offset) => {
          const ema = emaByIndex.get(start.startIndex + offset)
          return ema !== undefined && (direction === 'bullish' ? bar.close >= ema : bar.close <= ema)
        }).length
        patterns.push({
          id: `trend-pattern:small-pullback:${start.startIndex}:${end.endIndex}:${direction}`,
          kind: 'small-pullback-trend',
          label: direction === 'bullish' ? 'Bull Small Pullback Trend' : 'Bear Small Pullback Trend',
          direction,
          status: 'active',
          startIndex: start.startIndex,
          endIndex: bars.length - 1,
          knownAtIndex,
          statusKnownAtIndex: knownAtIndex,
          anchors: shallowAdvances.flatMap((leg) => [
            { index: leg.startIndex, price: leg.startPrice, role: 'advance-start' },
            { index: leg.endIndex, price: leg.endPrice, role: 'advance-end' },
          ]),
          score: clamp01(
            0.5 +
              shallowAdvances.length * 0.1 +
              (sameSideBars / Math.max(1, end.endIndex - start.startIndex + 1)) * 0.15,
          ),
          evidence: [`${shallowAdvances.length} 次浅而短的回调`, '顺势推进持续恢复'],
          warnings: ['等待深回调可能错过趋势，但不构成追价建议'],
          provenance: 'implementation-proxy',
        })
      }
    }
    if (marketState.state === 'broad-bull-channel' || marketState.state === 'broad-bear-channel') {
      const recent = uniqueLegs.slice(-6)
      if (recent.length >= 4) {
        const direction = marketState.state === 'broad-bull-channel' ? 'bullish' : 'bearish'
        patterns.push({
          id: `trend-pattern:broad-channel:${recent[0].startIndex}:${direction}`,
          kind: 'broad-channel',
          label: direction === 'bullish' ? 'Broad Bull Channel' : 'Broad Bear Channel',
          direction,
          status: 'active',
          startIndex: recent[0].startIndex,
          endIndex: bars.length - 1,
          knownAtIndex: recent[recent.length - 1].knownAtIndex,
          statusKnownAtIndex: recent[recent.length - 1].knownAtIndex,
          anchors: recent.map((leg) => ({ index: leg.endIndex, price: leg.endPrice, role: 'stair-step' })),
          score: marketState.score,
          evidence: ['市场保持净方向，但深回调和双向价格腿增加'],
          warnings: ['结构更接近倾斜交易区间，不按强趋势处理'],
          provenance: 'implementation-proxy',
        })
      }
    }
    return patterns.sort((left, right) => left.knownAtIndex - right.knownAtIndex).slice(-8)
  }

  function doublePatternLifecycle(
    bars,
    knownAtIndex,
    direction,
    necklinePrice,
    invalidationPrice,
    tolerance,
  ) {
    for (let index = knownAtIndex; index < bars.length; index += 1) {
      const invalidated =
        direction === 'bearish'
          ? bars[index].close > invalidationPrice + tolerance
          : bars[index].close < invalidationPrice - tolerance
      if (invalidated) {
        return {
          status: 'failed',
          statusKnownAtIndex: index,
          triggerIndex: null,
          triggerPrice: necklinePrice,
          endIndex: index,
          warnings: ['第二次测试继续扩张，双重测试候选失效'],
        }
      }
      const triggered =
        direction === 'bearish'
          ? bars[index].close < necklinePrice - tolerance
          : bars[index].close > necklinePrice + tolerance
      if (!triggered) continue
      const followEnd = Math.min(bars.length - 1, index + BREAKOUT_FOLLOW_THROUGH_WINDOW)
      for (let followIndex = index + 1; followIndex <= followEnd; followIndex += 1) {
        const returned =
          direction === 'bearish'
            ? bars[followIndex].close > necklinePrice + tolerance
            : bars[followIndex].close < necklinePrice - tolerance
        if (returned) {
          return {
            status: 'failed',
            statusKnownAtIndex: followIndex,
            triggerIndex: index,
            triggerPrice: necklinePrice,
            endIndex: followIndex,
            warnings: ['颈线突破缺乏跟进并重新回到原结构'],
          }
        }
        const followed =
          direction === 'bearish'
            ? bars[followIndex].close < bars[index].low
            : bars[followIndex].close > bars[index].high
        if (followed) {
          return {
            status: 'confirmed',
            statusKnownAtIndex: followIndex,
            triggerIndex: index,
            triggerPrice: necklinePrice,
            endIndex: followIndex,
            warnings: [],
          }
        }
      }
      return {
        status: 'active',
        statusKnownAtIndex: index,
        triggerIndex: index,
        triggerPrice: necklinePrice,
        endIndex: bars.length - 1,
        warnings: ['颈线已突破，仍在等待反向跟进'],
      }
    }
    return {
      status: 'candidate',
      statusKnownAtIndex: knownAtIndex,
      triggerIndex: null,
      triggerPrice: necklinePrice,
      endIndex: bars.length - 1,
      warnings: ['颈线尚未突破，只能称为双重测试候选'],
    }
  }

  function doublePullbackPattern(bars, parent, necklinePrice, tolerance) {
    if (parent.status !== 'confirmed' || parent.triggerIndex === null) return null
    for (let index = parent.statusKnownAtIndex + 1; index < bars.length; index += 1) {
      const tested =
        parent.direction === 'bearish'
          ? bars[index].high >= necklinePrice - tolerance && bars[index].close <= necklinePrice + tolerance
          : bars[index].low <= necklinePrice + tolerance && bars[index].close >= necklinePrice - tolerance
      if (!tested) continue
      const resumeIndex =
        Array.from({ length: Math.min(3, bars.length - index - 1) }, (_, offset) => index + offset + 1).find(
          (candidate) =>
            parent.direction === 'bearish'
              ? bars[candidate].close < bars[index].low
              : bars[candidate].close > bars[index].high,
        ) ?? null
      const confirmed = resumeIndex !== null
      return {
        id: `${parent.id}:pullback:${index}`,
        kind: parent.kind === 'double-top' ? 'double-top-pullback' : 'double-bottom-pullback',
        label: parent.kind === 'double-top' ? 'Double Top Pullback' : 'Double Bottom Pullback',
        direction: parent.direction,
        status: confirmed ? 'confirmed' : 'active',
        startIndex: parent.triggerIndex,
        endIndex: resumeIndex ?? bars.length - 1,
        knownAtIndex: index,
        statusKnownAtIndex: resumeIndex ?? index,
        triggerIndex: resumeIndex,
        triggerPrice: parent.direction === 'bearish' ? bars[index].low : bars[index].high,
        invalidationPrice: parent.direction === 'bearish' ? bars[index].high : bars[index].low,
        anchors: [
          { index: parent.triggerIndex, price: necklinePrice, role: 'neckline-break' },
          { index, price: necklinePrice, role: 'breakout-pullback' },
        ],
        score: clamp01(parent.score + (confirmed ? 0.1 : 0)),
        evidence: ['颈线突破后首次回测', ...(confirmed ? ['新方向再次恢复'] : [])],
        warnings: confirmed ? [] : ['回测后尚未出现新方向恢复'],
        provenance: 'book-concept',
      }
    }
    return null
  }

  function findDoubleTopBottomPatterns(bars, swings) {
    const patterns = []
    for (const type of ['high', 'low']) {
      const tests = swings.filter((point) => point.type === type)
      for (let index = 1; index < tests.length; index += 1) {
        const first = tests[index - 1]
        const second = tests[index]
        if (second.index - first.index < LEVEL_TOUCH_SEPARATION) continue
        const middle = swings
          .filter((point) => point.type !== type && point.index > first.index && point.index < second.index)
          .sort((left, right) => (type === 'high' ? left.price - right.price : right.price - left.price))[0]
        if (!middle) continue
        const knownAtIndex = Math.max(first.knownAtIndex, second.knownAtIndex, middle.knownAtIndex)
        if (knownAtIndex >= bars.length) continue
        const atr = Math.max(calculateAtr(bars.slice(0, knownAtIndex + 1)), DEFAULT_TICK_SIZE)
        const tolerance = Math.max(atr * 0.6, Math.abs(second.price) * 0.004)
        if (Math.abs(second.price - first.price) > tolerance) continue
        const direction = type === 'high' ? 'bearish' : 'bullish'
        const necklinePrice = middle.price
        const invalidationPrice =
          type === 'high'
            ? Math.max(first.price, second.price) + tolerance
            : Math.min(first.price, second.price) - tolerance
        const lifecycle = doublePatternLifecycle(
          bars,
          knownAtIndex,
          direction,
          necklinePrice,
          invalidationPrice,
          tolerance * 0.2,
        )
        const pattern = {
          id: `reversal:double-${type === 'high' ? 'top' : 'bottom'}:${first.index}:${second.index}`,
          kind: type === 'high' ? 'double-top' : 'double-bottom',
          label: type === 'high' ? 'Double Top' : 'Double Bottom',
          direction,
          startIndex: first.index,
          knownAtIndex,
          invalidationPrice,
          anchors: [
            { index: first.index, price: first.price, role: 'first-test' },
            { index: middle.index, price: middle.price, role: 'neckline' },
            { index: second.index, price: second.price, role: 'second-test' },
          ],
          score: clamp01(
            0.5 +
              (1 - Math.abs(second.price - first.price) / tolerance) * 0.25 +
              Math.min(second.index - first.index, 20) / 100,
          ),
          evidence: ['两次测试同一价格区域', '中间反向摆动定义颈线'],
          provenance: 'implementation-proxy',
          ...lifecycle,
        }
        patterns.push(pattern)
        const pullback = doublePullbackPattern(bars, pattern, necklinePrice, tolerance * 0.5)
        if (pullback) patterns.push(pullback)
      }
    }
    return patterns.sort((left, right) => left.knownAtIndex - right.knownAtIndex).slice(-10)
  }

  function reversalBreakLifecycle(bars, knownAtIndex, direction, triggerPrice, invalidationPrice, tolerance) {
    for (let index = knownAtIndex; index < bars.length; index += 1) {
      const invalidated =
        direction === 'bearish'
          ? bars[index].close > invalidationPrice + tolerance
          : bars[index].close < invalidationPrice - tolerance
      if (invalidated) {
        return {
          status: 'failed',
          statusKnownAtIndex: index,
          triggerIndex: null,
          triggerPrice,
          endIndex: index,
          warnings: ['价格继续沿原推动方向扩张，候选失效'],
        }
      }
      const triggered =
        direction === 'bearish'
          ? bars[index].close < triggerPrice - tolerance
          : bars[index].close > triggerPrice + tolerance
      if (!triggered) continue
      const followEnd = Math.min(bars.length - 1, index + BREAKOUT_FOLLOW_THROUGH_WINDOW)
      for (let followIndex = index + 1; followIndex <= followEnd; followIndex += 1) {
        const followed =
          direction === 'bearish'
            ? bars[followIndex].close < bars[index].low
            : bars[followIndex].close > bars[index].high
        if (followed) {
          return {
            status: 'confirmed',
            statusKnownAtIndex: followIndex,
            triggerIndex: index,
            triggerPrice,
            endIndex: followIndex,
            warnings: [],
          }
        }
        const failed =
          direction === 'bearish'
            ? bars[followIndex].close > invalidationPrice
            : bars[followIndex].close < invalidationPrice
        if (failed) {
          return {
            status: 'failed',
            statusKnownAtIndex: followIndex,
            triggerIndex: index,
            triggerPrice,
            endIndex: followIndex,
            warnings: ['反向触发后没有跟进'],
          }
        }
      }
      return {
        status: 'active',
        statusKnownAtIndex: index,
        triggerIndex: index,
        triggerPrice,
        endIndex: bars.length - 1,
        warnings: ['反向触发已出现，仍在等待跟进'],
      }
    }
    return {
      status: 'candidate',
      statusKnownAtIndex: knownAtIndex,
      triggerIndex: null,
      triggerPrice,
      endIndex: bars.length - 1,
      warnings: ['三次推动完成，但尚无反向触发'],
    }
  }

  function wedgePatterns(bars, swings, trend) {
    const patterns = []
    for (const type of ['high', 'low']) {
      const pushes = swings.filter((point) => point.type === type)
      for (let index = 2; index < pushes.length; index += 1) {
        const selected = pushes.slice(index - 2, index + 1)
        const pullbacks = [0, 1].map(
          (offset) =>
            swings
              .filter(
                (point) =>
                  point.type !== type &&
                  point.index > selected[offset].index &&
                  point.index < selected[offset + 1].index,
              )
              .sort((left, right) =>
                type === 'high' ? left.price - right.price : right.price - left.price,
              )[0],
        )
        if (pullbacks.some((point) => !point)) continue
        const knownAtIndex = Math.max(
          ...selected.map((point) => point.knownAtIndex),
          ...pullbacks.map((point) => point.knownAtIndex),
        )
        if (knownAtIndex >= bars.length) continue
        const atr = Math.max(calculateAtr(bars.slice(0, knownAtIndex + 1)), DEFAULT_TICK_SIZE)
        const tolerance = Math.max(atr * 0.45, Math.abs(selected[2].price) * 0.003)
        const advances =
          type === 'high'
            ? selected[2].price >= selected[0].price - tolerance
            : selected[2].price <= selected[0].price + tolerance
        if (!advances) continue
        const isPullback = (type === 'low' && trend === 'up') || (type === 'high' && trend === 'down')
        const direction = isPullback
          ? trend === 'up'
            ? 'bullish'
            : 'bearish'
          : type === 'high'
            ? 'bearish'
            : 'bullish'
        const triggerPrice = pullbacks[1].price
        const invalidationPrice =
          type === 'high' ? selected[2].price + tolerance : selected[2].price - tolerance
        const lifecycle = reversalBreakLifecycle(
          bars,
          knownAtIndex,
          direction,
          triggerPrice,
          invalidationPrice,
          tolerance * 0.2,
        )
        const firstPush = Math.abs(selected[0].price - pullbacks[0].price)
        const lastPush = Math.abs(selected[2].price - pullbacks[1].price)
        const diminishing = lastPush <= firstPush + tolerance
        const kind = isPullback ? 'wedge-pullback' : type === 'high' ? 'wedge-top' : 'wedge-bottom'
        patterns.push({
          id: `reversal:${kind}:${selected[0].index}:${selected[2].index}`,
          kind,
          label:
            kind === 'wedge-pullback' ? 'Wedge Pullback' : type === 'high' ? 'Wedge Top' : 'Wedge Bottom',
          direction,
          startIndex: selected[0].index,
          knownAtIndex,
          invalidationPrice,
          anchors: selected.map((point, anchorIndex) => ({
            index: point.index,
            price: point.price,
            role: `W${anchorIndex + 1}`,
          })),
          score: clamp01(
            0.55 +
              (diminishing ? 0.12 : 0) +
              selected.filter((point) => point.level === 'major').length * 0.05,
          ),
          evidence: ['三次同向推动由两次反向回调分隔', ...(diminishing ? ['第三推边际幅度未扩大'] : [])],
          provenance: 'implementation-proxy',
          ...lifecycle,
        })
      }
    }
    return patterns
  }

  function expandingTrianglePatterns(bars, swings) {
    const patterns = []
    const ordered = swings.slice().sort((left, right) => left.index - right.index)
    for (let end = 4; end < ordered.length; end += 1) {
      const points = ordered.slice(end - 4, end + 1)
      if (alternatingSwingLegs(points) !== 4) continue
      const highs = points.filter((point) => point.type === 'high')
      const lows = points.filter((point) => point.type === 'low')
      if (highs.length < 2 || lows.length < 2) continue
      const expandingHighs = highs
        .slice(1)
        .every((point, index) => comparePrices(point.price, highs[index].price) > 0)
      const expandingLows = lows
        .slice(1)
        .every((point, index) => comparePrices(point.price, lows[index].price) < 0)
      if (!expandingHighs || !expandingLows) continue
      const last = points[points.length - 1]
      const priorOpposite = points
        .slice(0, -1)
        .reverse()
        .find((point) => point.type !== last.type)
      if (!priorOpposite) continue
      const knownAtIndex = Math.max(...points.map((point) => point.knownAtIndex))
      if (knownAtIndex >= bars.length) continue
      const atr = Math.max(calculateAtr(bars.slice(0, knownAtIndex + 1)), DEFAULT_TICK_SIZE)
      const tolerance = Math.max(atr * 0.25, Math.abs(last.price) * 0.002)
      const direction = last.type === 'high' ? 'bearish' : 'bullish'
      const invalidationPrice = last.type === 'high' ? last.price + tolerance : last.price - tolerance
      const lifecycle = reversalBreakLifecycle(
        bars,
        knownAtIndex,
        direction,
        priorOpposite.price,
        invalidationPrice,
        tolerance * 0.2,
      )
      patterns.push({
        id: `reversal:expanding-triangle:${points[0].index}:${last.index}`,
        kind: 'expanding-triangle',
        label: 'Expanding Triangle',
        direction,
        startIndex: points[0].index,
        knownAtIndex,
        invalidationPrice,
        anchors: points.map((point, index) => ({
          index: point.index,
          price: point.price,
          role: `push-${index + 1}`,
        })),
        score: clamp01(0.55 + points.filter((point) => point.level === 'major').length * 0.05),
        evidence: ['五次交替推动形成更高高点与更低低点'],
        provenance: 'implementation-proxy',
        ...lifecycle,
      })
    }
    return patterns
  }

  function findWedgeAndExpandingPatterns(bars, swings, trend) {
    return [...wedgePatterns(bars, swings, trend), ...expandingTrianglePatterns(bars, swings)]
      .sort((left, right) => left.knownAtIndex - right.knownAtIndex)
      .slice(-12)
  }

  function findMajorTrendReversalPatterns(bars, trendLines, channelEvents, swings, breakouts, marketState) {
    const patterns = []
    trendLines
      .filter((line) => line.rank === 'primary' && line.validUntilIndex !== null)
      .forEach((line) => {
        const breakIndex = line.validUntilIndex
        const originalBull = line.direction === 'ascending'
        const direction = originalBull ? 'bearish' : 'bullish'
        const relevantBars = bars.slice(line.anchorIndex, breakIndex + 1)
        if (relevantBars.length === 0) return
        const oldExtremePrice = originalBull
          ? Math.max(...relevantBars.map((bar) => bar.high))
          : Math.min(...relevantBars.map((bar) => bar.low))
        const oldExtremeOffset = relevantBars.findIndex((bar) =>
          originalBull ? bar.high === oldExtremePrice : bar.low === oldExtremePrice,
        )
        const oldExtremeIndex = line.anchorIndex + oldExtremeOffset
        const atr = Math.max(calculateAtr(bars.slice(0, breakIndex + 1)), DEFAULT_TICK_SIZE)
        const tolerance = Math.max(atr * 0.35, Math.abs(oldExtremePrice) * 0.0025)
        const matchingTestEvent = channelEvents.find(
          (event) =>
            event.type === 'break-and-test' &&
            event.lineStartIndex === line.startIndex &&
            event.lineAnchorIndex === line.anchorIndex &&
            event.knownAtIndex >= breakIndex,
        )
        let testIndex = matchingTestEvent?.knownAtIndex ?? null
        if (testIndex === null) {
          for (let index = breakIndex + 1; index < bars.length; index += 1) {
            const tested = originalBull
              ? bars[index].high >= oldExtremePrice - tolerance
              : bars[index].low <= oldExtremePrice + tolerance
            if (tested) {
              testIndex = index
              break
            }
          }
        }
        const failedTestSwing =
          testIndex === null
            ? undefined
            : swings.find(
                (point) =>
                  point.type === (originalBull ? 'high' : 'low') &&
                  point.knownAtIndex > testIndex &&
                  (originalBull
                    ? point.price < oldExtremePrice - tolerance * 0.2
                    : point.price > oldExtremePrice + tolerance * 0.2),
              )
        const oppositeBreakout = failedTestSwing
          ? breakouts.find(
              (breakout) =>
                breakout.direction === direction &&
                breakout.knownAtIndex >= failedTestSwing.knownAtIndex &&
                breakout.followThrough !== 'failed',
            )
          : undefined
        let stage = 'trend-line-break'
        let status = 'candidate'
        let statusKnownAtIndex = breakIndex
        const anchors = [
          { index: breakIndex, price: bars[breakIndex].close, role: 'trend-line-break' },
          { index: oldExtremeIndex, price: oldExtremePrice, role: 'old-extreme' },
        ]
        const evidence = ['主要趋势线已经发生收盘突破']
        const warnings = []
        if (testIndex !== null) {
          stage = 'old-extreme-test'
          status = 'active'
          statusKnownAtIndex = testIndex
          anchors.push({ index: testIndex, price: oldExtremePrice, role: 'old-extreme-test' })
          evidence.push('趋势线突破后重新测试原趋势极值')
        }
        if (failedTestSwing) {
          stage = 'failed-test'
          statusKnownAtIndex = failedTestSwing.knownAtIndex
          anchors.push({ index: failedTestSwing.index, price: failedTestSwing.price, role: 'failed-test' })
          evidence.push(originalBull ? '旧高测试形成更低高点' : '旧低测试形成更高低点')
        }
        if (oppositeBreakout) {
          stage = 'opposite-breakout'
          statusKnownAtIndex = oppositeBreakout.knownAtIndex
          anchors.push({
            index: oppositeBreakout.closeBreakIndex ?? oppositeBreakout.startedAtIndex,
            price: oppositeBreakout.boundaryPrice,
            role: 'opposite-breakout',
          })
          evidence.push('新方向突破具体结构边界')
          if (oppositeBreakout.followThrough === 'strong' || oppositeBreakout.followThrough === 'weak') {
            stage =
              oppositeBreakout.phase === 'continuation' || oppositeBreakout.phase === 'breakout-retest'
                ? 'confirmed'
                : 'follow-through'
            status = 'confirmed'
            statusKnownAtIndex = oppositeBreakout.followThroughIndex ?? oppositeBreakout.knownAtIndex
            evidence.push('反向突破获得跟进')
          }
        }
        if (
          status !== 'confirmed' &&
          (marketState.state === 'trading-range' || marketState.state === 'tight-trading-range')
        ) {
          stage = 'trading-range'
          warnings.push('旧趋势减弱后进入交易区间，尚未建立反向趋势')
        } else if (status !== 'confirmed') {
          warnings.push('当前阶段不足以确认主要趋势反转')
        }
        patterns.push({
          id: `reversal:mtr:${line.startIndex}:${line.anchorIndex}:${breakIndex}`,
          kind: 'major-trend-reversal',
          label: 'Major Trend Reversal',
          direction,
          status,
          stage,
          startIndex: line.startIndex,
          endIndex: status === 'confirmed' ? statusKnownAtIndex : bars.length - 1,
          knownAtIndex: breakIndex,
          statusKnownAtIndex,
          triggerIndex: oppositeBreakout?.closeBreakIndex ?? null,
          triggerPrice: oppositeBreakout?.boundaryPrice ?? null,
          invalidationPrice: originalBull ? oldExtremePrice + tolerance : oldExtremePrice - tolerance,
          anchors,
          score: clamp01(0.25 + anchors.length * 0.13 + (status === 'confirmed' ? 0.2 : 0)),
          evidence,
          warnings,
          provenance: 'book-concept',
        })
      })
    return patterns.sort((left, right) => left.knownAtIndex - right.knownAtIndex).slice(-4)
  }

  function findFinalFlagPatterns(bars, tradingRanges) {
    return tradingRanges
      .filter(
        (range) =>
          range.status === 'failed-breakout' &&
          range.breakoutIndex !== null &&
          range.breakoutDirection !== null &&
          range.failedBreakoutIndex !== null,
      )
      .map((range) => {
        const direction = range.breakoutDirection === 'bullish' ? 'bearish' : 'bullish'
        const triggerPrice = direction === 'bearish' ? range.low : range.high
        const height = range.high - range.low
        const invalidationPrice =
          direction === 'bearish' ? range.high + height * 0.15 : range.low - height * 0.15
        const lifecycle = reversalBreakLifecycle(
          bars,
          range.failedBreakoutIndex,
          direction,
          triggerPrice,
          invalidationPrice,
          Math.max(DEFAULT_TICK_SIZE, height * 0.03),
        )
        return {
          id: `reversal:final-flag:${range.id}`,
          kind: 'final-flag',
          label: lifecycle.status === 'confirmed' ? 'Final Flag' : 'Possible Final Flag',
          direction,
          startIndex: range.startIndex,
          knownAtIndex: range.failedBreakoutIndex,
          invalidationPrice,
          anchors: [
            { index: range.startIndex, price: triggerPrice, role: 'flag-start' },
            {
              index: range.breakoutIndex,
              price: range.breakoutDirection === 'bullish' ? range.high : range.low,
              role: 'trend-breakout',
            },
            {
              index: range.failedBreakoutIndex,
              price: range.breakoutDirection === 'bullish' ? range.high : range.low,
              role: 'failed-breakout',
            },
          ],
          score: clamp01(range.score + (lifecycle.status === 'confirmed' ? 0.15 : 0)),
          evidence: ['成熟区间顺原突破后迅速失败'],
          provenance: 'implementation-proxy',
          ...lifecycle,
          status: lifecycle.status === 'candidate' ? 'active' : lifecycle.status,
        }
      })
      .slice(-4)
  }

  function findClimacticReversalPatterns(bars, classifications) {
    const patterns = []
    for (let index = 20; index < bars.length; index += 1) {
      const classification = classifications[index]
      if (!classification?.isTrendBar || classification.direction === 'neutral') continue
      const medianVolume = median(
        bars
          .slice(index - 20, index)
          .map((bar) => bar.volume)
          .filter((volume) => volume > 0),
      )
      const relativeVolume = medianVolume > 0 ? bars[index].volume / medianVolume : 0
      if (relativeVolume < 2.5 || classification.features.relativeRange < 1.8) continue
      const direction = classification.direction === 'bullish' ? 'bearish' : 'bullish'
      const opposite = classifications
        .slice(index + 1, index + 4)
        .find((candidate) => candidate.isTrendBar && candidate.direction === direction)
      const oppositeIndex = opposite?.index ?? null
      const followIndex =
        oppositeIndex === null
          ? null
          : (classifications
              .slice(oppositeIndex + 1, oppositeIndex + 4)
              .find(
                (candidate) => candidate.direction === direction && candidate.features.closeLocation !== 0.5,
              )?.index ?? null)
      const status = followIndex !== null ? 'confirmed' : oppositeIndex !== null ? 'active' : 'candidate'
      const originalBull = classification.direction === 'bullish'
      patterns.push({
        id: `reversal:climax:${index}`,
        kind: 'climactic-reversal',
        label: 'Climactic Reversal',
        direction,
        status,
        startIndex: index,
        endIndex: followIndex ?? bars.length - 1,
        knownAtIndex: index,
        statusKnownAtIndex: followIndex ?? oppositeIndex ?? index,
        triggerIndex: oppositeIndex,
        triggerPrice: oppositeIndex === null ? null : bars[oppositeIndex].close,
        invalidationPrice: originalBull ? bars[index].high : bars[index].low,
        anchors: [
          { index, price: originalBull ? bars[index].high : bars[index].low, role: 'climax' },
          ...(oppositeIndex === null
            ? []
            : [{ index: oppositeIndex, price: bars[oppositeIndex].close, role: 'opposite-spike' }]),
        ],
        score: clamp01(0.45 + Math.min(relativeVolume / 5, 0.3) + (followIndex !== null ? 0.2 : 0)),
        evidence: [`相对成交量 ${relativeVolume.toFixed(1)} 倍`, '趋势 K 线振幅显著扩张'],
        warnings: status === 'confirmed' ? [] : ['异常放量本身不决定反转，仍需价格跟进'],
        provenance: 'implementation-proxy',
      })
    }
    return patterns.slice(-4)
  }

  function buildLevelClusters(swings, tolerance) {
    const sorted = swings
      .slice(-30)
      .sort((left, right) => left.price - right.price || left.index - right.index)
    const clusters = []
    sorted.forEach((point) => {
      const matched = clusters.find((cluster) => {
        const clusterAverage = average(cluster.prices)
        const clusterLow = cluster.prices[0]
        return (
          Math.abs(clusterAverage - point.price) <= tolerance && point.price - clusterLow <= tolerance * 2
        )
      })
      if (matched) {
        matched.prices.push(point.price)
        matched.indices.push(point.index)
        return
      }
      clusters.push({ prices: [point.price], indices: [point.index] })
    })
    return clusters
  }

  function distinctTouchIndices(indices) {
    const touches = []
    indices
      .slice()
      .sort((left, right) => left - right)
      .forEach((index) => {
        const previous = touches[touches.length - 1]
        if (previous === undefined || index - previous >= LEVEL_TOUCH_SEPARATION) {
          touches.push(index)
        }
      })
    return touches
  }

  function selectLevels(levels, currentPrice, atr) {
    if (levels.length <= 3) {
      return levels
        .slice()
        .sort((left, right) => Math.abs(currentPrice - left.price) - Math.abs(currentPrice - right.price))
    }
    const byDistance = levels
      .slice()
      .sort((left, right) => Math.abs(currentPrice - left.price) - Math.abs(currentPrice - right.price))
    const nearest = byDistance[0]
    const latestIndex = Math.max(...levels.map((level) => level.lastTouchedIndex), 1)
    const normalizedAtr = Math.max(atr, currentPrice * 0.0025, tickSize)
    const remaining = byDistance.slice(1).sort((left, right) => {
      const leftScore =
        left.touches * 2 +
        left.lastTouchedIndex / latestIndex -
        (Math.abs(currentPrice - left.price) / normalizedAtr) * 0.15
      const rightScore =
        right.touches * 2 +
        right.lastTouchedIndex / latestIndex -
        (Math.abs(currentPrice - right.price) / normalizedAtr) * 0.15
      return rightScore - leftScore
    })
    return [nearest, ...remaining.slice(0, 2)]
  }

  function clusterLevels(swings, currentPrice, atr) {
    const tolerance = Math.max(atr * 0.45, currentPrice * 0.0025)
    const clusters = buildLevelClusters(swings, tolerance)
    const levels = clusters
      .map((cluster) => {
        const indices = distinctTouchIndices(cluster.indices)
        if (indices.length < 2) {
          return null
        }
        const price = average(cluster.prices)
        const touches = indices.length
        const zonePadding = tolerance * 0.25
        return {
          price,
          zoneLow: Math.min(...cluster.prices) - zonePadding,
          zoneHigh: Math.max(...cluster.prices) + zonePadding,
          type: price <= currentPrice ? 'support' : 'resistance',
          touches,
          strength: Math.min(3, touches),
          lastTouchedIndex: indices[indices.length - 1],
          label: '',
        }
      })
      .filter((level) => level !== null)

    const nearestSupport = swings
      .filter((point) => point.type === 'low' && point.price < currentPrice)
      .sort((left, right) => right.price - left.price)[0]
    const nearestResistance = swings
      .filter((point) => point.type === 'high' && point.price > currentPrice)
      .sort((left, right) => left.price - right.price)[0]
    if (
      nearestSupport &&
      !levels.some((level) => Math.abs(level.price - nearestSupport.price) <= tolerance)
    ) {
      levels.push({
        price: nearestSupport.price,
        zoneLow: nearestSupport.price - tolerance * 0.35,
        zoneHigh: nearestSupport.price + tolerance * 0.35,
        type: 'support',
        touches: 1,
        strength: 1,
        lastTouchedIndex: nearestSupport.index,
        label: '',
      })
    }
    if (
      nearestResistance &&
      !levels.some((level) => Math.abs(level.price - nearestResistance.price) <= tolerance)
    ) {
      levels.push({
        price: nearestResistance.price,
        zoneLow: nearestResistance.price - tolerance * 0.35,
        zoneHigh: nearestResistance.price + tolerance * 0.35,
        type: 'resistance',
        touches: 1,
        strength: 1,
        lastTouchedIndex: nearestResistance.index,
        label: '',
      })
    }

    const supports = selectLevels(
      levels.filter((level) => level.type === 'support'),
      currentPrice,
      atr,
    ).sort((left, right) => right.price - left.price)
    const resistances = selectLevels(
      levels.filter((level) => level.type === 'resistance'),
      currentPrice,
      atr,
    ).sort((left, right) => left.price - right.price)
    supports.forEach((level, index) => {
      level.label = `S${index + 1}`
    })
    resistances.forEach((level, index) => {
      level.label = `R${index + 1}`
    })
    return [...supports, ...resistances]
  }

  function lineValue(start, anchor, index) {
    const slope = (anchor.price - start.price) / (anchor.index - start.index)
    return start.price + slope * (index - start.index)
  }

  function anchoredLineValue(line, index) {
    return line.startPrice + line.slope * (index - line.startIndex)
  }

  function candidateRespectsLine(bars, points, start, anchor, knownAtIndex, direction) {
    const relevantPoints = points.filter((point) => point.index > start.index && point.index < anchor.index)
    const pointsRespectLine = relevantPoints.every((point) => {
      const projected = lineValue(start, anchor, point.index)
      const tolerance = comparisonTolerance(point.price, projected)
      return direction === 'ascending'
        ? point.price >= projected - tolerance
        : point.price <= projected + tolerance
    })
    if (!pointsRespectLine) {
      return false
    }
    for (let index = start.index + 1; index <= knownAtIndex; index += 1) {
      const projected = lineValue(start, anchor, index)
      const tolerance = comparisonTolerance(bars[index].close, projected)
      if (direction === 'ascending' && bars[index].close < projected - tolerance) {
        return false
      }
      if (direction === 'descending' && bars[index].close > projected + tolerance) {
        return false
      }
    }
    return true
  }

  function findTrendLineBreaks(bars, start, anchor, knownAtIndex, direction) {
    for (let index = knownAtIndex + 1; index < bars.length; index += 1) {
      const projected = lineValue(start, anchor, index)
      const tolerance = comparisonTolerance(bars[index].close, projected)
      const broken =
        direction === 'ascending'
          ? bars[index].close < projected - tolerance
          : bars[index].close > projected + tolerance
      if (broken) {
        return [index]
      }
    }
    return []
  }

  function trendLineTouches(points, start, anchor, endIndex) {
    return points.filter((point) => {
      if (point.index < start.index || point.index > endIndex) {
        return false
      }
      const projected = lineValue(start, anchor, point.index)
      return Math.abs(point.price - projected) <= comparisonTolerance(point.price, projected) * 2
    }).length
  }

  function buildTrendLineCandidate(bars, points, start, anchor, direction) {
    const comparison = comparePrices(anchor.price, start.price)
    const hasExpectedSlope = direction === 'ascending' ? comparison > 0 : comparison < 0
    const knownAtIndex = Math.max(start.confirmedIndex, anchor.confirmedIndex)
    if (
      !hasExpectedSlope ||
      knownAtIndex >= bars.length ||
      !candidateRespectsLine(bars, points, start, anchor, knownAtIndex, direction)
    ) {
      return null
    }

    const slope = (anchor.price - start.price) / (anchor.index - start.index)
    const breaks = findTrendLineBreaks(bars, start, anchor, knownAtIndex, direction)
    const validUntilIndex = breaks[0] ?? null
    const endIndex = validUntilIndex ?? bars.length - 1
    const touches = trendLineTouches(points, start, anchor, endIndex)
    const span = anchor.index - start.index
    const survival = Math.max(0, endIndex - knownAtIndex)
    const recency = anchor.index / Math.max(1, bars.length - 1)
    const score =
      touches * 3 + Math.min(span, 60) * 0.05 + Math.min(survival, 40) * 0.08 + recency * 2 - breaks.length

    return {
      kind: 'trend-line',
      startIndex: start.index,
      startPrice: start.price,
      anchorIndex: anchor.index,
      anchorPrice: anchor.price,
      knownAtIndex,
      slope,
      validUntilIndex,
      touches,
      breaks,
      endIndex,
      endPrice: lineValue(start, anchor, endIndex),
      direction,
      rank: 'secondary',
      score,
      label: direction === 'ascending' ? '上升趋势线' : '下降趋势线',
    }
  }

  function trendLineCandidates(bars, points, direction) {
    const recentPoints = points.slice(-8)
    const candidates = []
    for (let anchorIndex = 1; anchorIndex < recentPoints.length; anchorIndex += 1) {
      for (let startIndex = 0; startIndex < anchorIndex; startIndex += 1) {
        const candidate = buildTrendLineCandidate(
          bars,
          points,
          recentPoints[startIndex],
          recentPoints[anchorIndex],
          direction,
        )
        if (candidate) {
          candidates.push(candidate)
        }
      }
    }
    return candidates
  }

  function sameTrendLineGeometry(left, right, lastIndex) {
    if (left.direction !== right.direction) {
      return false
    }
    const leftStart = anchoredLineValue(left, 0)
    const rightStart = anchoredLineValue(right, 0)
    const leftEnd = anchoredLineValue(left, lastIndex)
    const rightEnd = anchoredLineValue(right, lastIndex)
    return (
      Math.abs(leftStart - rightStart) <= comparisonTolerance(leftStart, rightStart) &&
      Math.abs(leftEnd - rightEnd) <= comparisonTolerance(leftEnd, rightEnd)
    )
  }

  function findTrendLines(bars, swings, trend) {
    const candidates = []
    if (trend !== 'down') {
      candidates.push(
        ...trendLineCandidates(
          bars,
          swings.filter((point) => point.type === 'low'),
          'ascending',
        ),
      )
    }
    if (trend !== 'up') {
      candidates.push(
        ...trendLineCandidates(
          bars,
          swings.filter((point) => point.type === 'high'),
          'descending',
        ),
      )
    }

    const sorted = candidates.sort(
      (left, right) =>
        right.score - left.score ||
        right.anchorIndex - left.anchorIndex ||
        right.anchorIndex - right.startIndex - (left.anchorIndex - left.startIndex),
    )
    const distinct = sorted.filter(
      (line, index) =>
        !sorted.slice(0, index).some((candidate) => sameTrendLineGeometry(line, candidate, bars.length - 1)),
    )
    const selected = distinct.slice(0, MAX_TREND_LINE_CANDIDATES)
    if (trend === 'range') {
      const directions = ['ascending', 'descending']
      directions.forEach((direction) => {
        if (selected.some((line) => line.direction === direction)) {
          return
        }
        const bestMissingDirection = distinct.find((line) => line.direction === direction)
        if (!bestMissingDirection) {
          return
        }
        if (selected.length < MAX_TREND_LINE_CANDIDATES) {
          selected.push(bestMissingDirection)
        } else {
          selected[selected.length - 1] = bestMissingDirection
        }
      })
      selected.sort((left, right) => right.score - left.score)
    }

    return selected.map((line, index) => ({
      ...line,
      rank: index === 0 ? 'primary' : 'secondary',
      label: `${line.direction === 'ascending' ? '上升' : '下降'}${index === 0 ? '' : '次级'}趋势线`,
    }))
  }

  function buildTrendChannelLine(bars, trendLine) {
    let extremeIndex = trendLine.startIndex
    let extremePrice = trendLine.direction === 'ascending' ? bars[extremeIndex].high : bars[extremeIndex].low
    let offset = extremePrice - trendLine.startPrice

    for (let index = trendLine.startIndex + 1; index <= trendLine.knownAtIndex; index += 1) {
      const projected = anchoredLineValue(trendLine, index)
      const price = trendLine.direction === 'ascending' ? bars[index].high : bars[index].low
      const candidateOffset = price - projected
      const isMoreExtreme =
        trendLine.direction === 'ascending' ? candidateOffset > offset : candidateOffset < offset
      if (isMoreExtreme) {
        offset = candidateOffset
        extremeIndex = index
        extremePrice = price
      }
    }

    return {
      kind: 'channel-line',
      startIndex: trendLine.startIndex,
      startPrice: trendLine.startPrice + offset,
      anchorIndex: trendLine.anchorIndex,
      anchorPrice: trendLine.anchorPrice + offset,
      knownAtIndex: trendLine.knownAtIndex,
      slope: trendLine.slope,
      validUntilIndex: trendLine.validUntilIndex,
      touches: 1,
      breaks: [],
      endIndex: trendLine.endIndex,
      endPrice: trendLine.endPrice + offset,
      direction: trendLine.direction,
      rank: trendLine.rank,
      score: trendLine.score,
      label: trendLine.direction === 'ascending' ? '上升通道线' : '下降通道线',
      offset,
      extremeIndex,
      extremePrice,
    }
  }

  function channelEventLabel(type) {
    if (type === 'touch') return 'Touch'
    if (type === 'overshoot') return 'Overshoot'
    if (type === 'undershoot') return 'Undershoot'
    if (type === 'trend-line-break') return 'Trend-line Break'
    return 'Break and Test'
  }

  function createTrendChannelEvent(type, index, knownAtIndex, price, boundaryPrice, line) {
    return {
      type,
      label: channelEventLabel(type),
      index,
      knownAtIndex,
      price,
      boundaryPrice,
      direction: line.direction,
      lineStartIndex: line.startIndex,
      lineAnchorIndex: line.anchorIndex,
      lineRank: line.rank,
    }
  }

  function trendChannelEvents(bars, swings, trendLine, channelLine) {
    const events = []
    const activeUntilIndex = trendLine.validUntilIndex ?? bars.length - 1
    let overshooting = false
    let lastTouchIndex = -3
    for (let index = channelLine.knownAtIndex + 1; index <= activeUntilIndex; index += 1) {
      const boundary = anchoredLineValue(channelLine, index)
      const price = trendLine.direction === 'ascending' ? bars[index].high : bars[index].low
      const tolerance = Math.max(
        comparisonTolerance(price, boundary) * 2,
        calculateAtr(bars.slice(0, index + 1)) * 0.08,
      )
      const distance = trendLine.direction === 'ascending' ? price - boundary : boundary - price
      if (distance > tolerance) {
        if (!overshooting) {
          events.push(createTrendChannelEvent('overshoot', index, index, price, boundary, trendLine))
        }
        overshooting = true
        continue
      }
      overshooting = false
      if (Math.abs(distance) <= tolerance && index - lastTouchIndex >= 3) {
        events.push(createTrendChannelEvent('touch', index, index, price, boundary, trendLine))
        lastTouchIndex = index
      }
    }

    const advancingType = trendLine.direction === 'ascending' ? 'high' : 'low'
    swings
      .filter(
        (point) =>
          point.type === advancingType &&
          point.index > channelLine.knownAtIndex &&
          point.confirmedIndex <= activeUntilIndex,
      )
      .forEach((point) => {
        const boundary = anchoredLineValue(channelLine, point.index)
        const tolerance = Math.max(
          comparisonTolerance(point.price, boundary) * 2,
          calculateAtr(bars.slice(0, point.confirmedIndex + 1)) * 0.08,
        )
        const missedBy = trendLine.direction === 'ascending' ? boundary - point.price : point.price - boundary
        if (missedBy > tolerance) {
          events.push(
            createTrendChannelEvent(
              'undershoot',
              point.index,
              point.confirmedIndex,
              point.price,
              boundary,
              trendLine,
            ),
          )
        }
      })

    const breakIndex = trendLine.validUntilIndex
    if (breakIndex !== null) {
      const breakBoundary = anchoredLineValue(trendLine, breakIndex)
      events.push(
        createTrendChannelEvent(
          'trend-line-break',
          breakIndex,
          breakIndex,
          bars[breakIndex].close,
          breakBoundary,
          trendLine,
        ),
      )

      const advancePrices = bars
        .slice(trendLine.anchorIndex, breakIndex + 1)
        .map((bar) => (trendLine.direction === 'ascending' ? bar.high : bar.low))
      const referencePrice =
        trendLine.direction === 'ascending' ? Math.max(...advancePrices) : Math.min(...advancePrices)
      for (let index = breakIndex + 1; index < bars.length; index += 1) {
        const price = trendLine.direction === 'ascending' ? bars[index].high : bars[index].low
        const tolerance = Math.max(
          comparisonTolerance(price, referencePrice) * 2,
          calculateAtr(bars.slice(0, index + 1)) * 0.08,
        )
        const tested =
          trendLine.direction === 'ascending'
            ? price >= referencePrice - tolerance
            : price <= referencePrice + tolerance
        if (tested) {
          events.push(
            createTrendChannelEvent('break-and-test', index, index, price, referencePrice, trendLine),
          )
          break
        }
      }
    }

    return events.sort((left, right) => left.knownAtIndex - right.knownAtIndex || left.index - right.index)
  }

  function findTrendChannelLines(bars, swings, trendLines) {
    const channelLines = []
    const eventMap = new Map()
    trendLines.forEach((trendLine) => {
      const provisionalLine = buildTrendChannelLine(bars, trendLine)
      const events = trendChannelEvents(bars, swings, trendLine, provisionalLine)
      const touches =
        events.filter((event) => event.type === 'touch' || event.type === 'overshoot').length + 1
      const breaks = events.filter((event) => event.type === 'overshoot').map((event) => event.index)
      channelLines.push({ ...provisionalLine, touches, breaks })
      events.forEach((event) => {
        const key = `${event.type}:${event.index}:${event.direction}`
        if (!eventMap.has(key)) {
          eventMap.set(key, event)
        }
      })
    })

    return {
      channelLines,
      channelEvents: Array.from(eventMap.values())
        .sort((left, right) => left.knownAtIndex - right.knownAtIndex || left.index - right.index)
        .slice(-MAX_CHANNEL_EVENTS),
    }
  }

  function microChannelPrice(bar, direction) {
    return direction === 'bullish' ? bar.low : bar.high
  }

  function microChannelContinues(referencePrice, nextPrice, direction, tickSize) {
    const epsilon = Number.EPSILON * Math.max(1, Math.abs(referencePrice), Math.abs(nextPrice)) * 16
    return direction === 'bullish'
      ? nextPrice >= referencePrice - tickSize - epsilon
      : nextPrice <= referencePrice + tickSize + epsilon
  }

  function microChannelMaxPullback(bars, startIndex, endIndex, direction) {
    let advancingExtreme = direction === 'bullish' ? bars[startIndex].high : bars[startIndex].low
    let maximum = 0
    for (let index = startIndex + 1; index <= endIndex; index += 1) {
      const bar = bars[index]
      if (direction === 'bullish') {
        maximum = Math.max(maximum, advancingExtreme - bar.low)
        advancingExtreme = Math.max(advancingExtreme, bar.high)
      } else {
        maximum = Math.max(maximum, bar.high - advancingExtreme)
        advancingExtreme = Math.min(advancingExtreme, bar.low)
      }
    }
    return maximum
  }

  function scanMicroChannels(bars, direction, tickSize) {
    if (bars.length === 0) {
      return []
    }
    const candidates = []
    let startIndex = 0
    let referencePrice = microChannelPrice(bars[0], direction)
    for (let index = 1; index <= bars.length; index += 1) {
      const nextPrice = index < bars.length ? microChannelPrice(bars[index], direction) : null
      const continues =
        nextPrice !== null && microChannelContinues(referencePrice, nextPrice, direction, tickSize)
      if (continues && nextPrice !== null) {
        referencePrice =
          direction === 'bullish' ? Math.max(referencePrice, nextPrice) : Math.min(referencePrice, nextPrice)
        continue
      }
      const endIndex = index - 1
      const length = endIndex - startIndex + 1
      if (length >= MICRO_CHANNEL_MIN_LENGTH) {
        const confirmedAtIndex = startIndex + MICRO_CHANNEL_MIN_LENGTH - 1
        const channel = {
          direction,
          startIndex,
          endIndex,
          startPrice: microChannelPrice(bars[startIndex], direction),
          endPrice: microChannelPrice(bars[endIndex], direction),
          confirmedAtIndex,
          knownAtIndex: confirmedAtIndex,
          lastUpdatedIndex: endIndex,
          length,
          maxPullback: microChannelMaxPullback(bars, startIndex, endIndex, direction),
          label: direction === 'bullish' ? '牛微型通道' : '熊微型通道',
        }
        const breakEvent =
          index < bars.length
            ? {
                index,
                knownAtIndex: index,
                price: microChannelPrice(bars[index], direction),
                direction: direction === 'bullish' ? 'bearish' : 'bullish',
                channelDirection: direction,
                channelStartIndex: startIndex,
                channelEndIndex: endIndex,
                label: 'micro-channel break',
              }
            : null
        candidates.push({ channel, breakEvent })
      }
      if (index < bars.length && nextPrice !== null) {
        startIndex = index
        referencePrice = nextPrice
      }
    }
    return candidates
  }

  function resolveMicroChannelConflicts(bars, candidates, tickSize) {
    const groups = new Map()
    candidates.forEach((candidate) => {
      const key = `${candidate.channel.startIndex}:${candidate.channel.endIndex}`
      const grouped = groups.get(key) || []
      grouped.push(candidate)
      groups.set(key, grouped)
    })
    const resolved = []
    groups.forEach((group) => {
      if (group.length < 2) {
        resolved.push(...group)
        return
      }
      const startIndex = group[0].channel.startIndex
      const endIndex = group[0].channel.endIndex
      const closeMove = bars[endIndex].close - bars[startIndex].close
      const preferredDirection = closeMove > tickSize ? 'bullish' : closeMove < -tickSize ? 'bearish' : null
      if (preferredDirection) {
        const preferred = group.find((candidate) => candidate.channel.direction === preferredDirection)
        if (preferred) {
          resolved.push(preferred)
        }
      }
    })
    return resolved
  }

  /**
   * Finds maximal runs of non-lower lows (bull) and non-higher highs (bear).
   * The tolerance is anchored to a non-retreating reference so small adverse
   * moves cannot accumulate into a channel pointing in the wrong direction.
   */
  function findMicroChannelAnalysis(bars, tickSize = DEFAULT_MICRO_CHANNEL_TICK_SIZE) {
    const normalizedTickSize =
      Number.isFinite(tickSize) && tickSize > 0 ? tickSize : DEFAULT_MICRO_CHANNEL_TICK_SIZE
    const candidates = resolveMicroChannelConflicts(
      bars,
      [
        ...scanMicroChannels(bars, 'bullish', normalizedTickSize),
        ...scanMicroChannels(bars, 'bearish', normalizedTickSize),
      ],
      normalizedTickSize,
    ).sort(
      (left, right) =>
        left.channel.startIndex - right.channel.startIndex ||
        left.channel.direction.localeCompare(right.channel.direction),
    )
    return {
      microChannels: candidates.map((candidate) => candidate.channel),
      microChannelBreaks: candidates
        .map((candidate) => candidate.breakEvent)
        .filter((event) => event !== null)
        .sort(
          (left, right) =>
            left.index - right.index || left.channelDirection.localeCompare(right.channelDirection),
        ),
    }
  }

  function findMicroChannels(bars, tickSize = DEFAULT_MICRO_CHANNEL_TICK_SIZE) {
    return findMicroChannelAnalysis(bars, tickSize).microChannels
  }

  function breakoutTolerance(bars, index, boundaryPrice) {
    const atr = calculateAtr(bars.slice(0, index + 1))
    return Math.max(comparisonTolerance(boundaryPrice, bars[index].close), atr * BREAKOUT_ATR_BUFFER_RATIO)
  }

  function intrabarBreaksBoundary(bar, direction, boundaryPrice, tolerance) {
    return direction === 'bullish'
      ? bar.high > boundaryPrice + tolerance
      : bar.low < boundaryPrice - tolerance
  }

  function closesBeyondBoundary(bar, direction, boundaryPrice, tolerance) {
    return direction === 'bullish'
      ? bar.close > boundaryPrice + tolerance
      : bar.close < boundaryPrice - tolerance
  }

  function closesInsideOldRange(bar, direction, boundaryPrice, tolerance) {
    return direction === 'bullish'
      ? bar.close < boundaryPrice - tolerance
      : bar.close > boundaryPrice + tolerance
  }

  function transitionPrice(bar, direction, phase, boundaryPrice) {
    if (phase === 'intrabar-break') {
      return direction === 'bullish' ? bar.high : bar.low
    }
    if (phase === 'breakout-retest') {
      return boundaryPrice
    }
    return bar.close
  }

  function appendBreakoutTransition(transitions, phase, index, bars, direction, boundaryPrice) {
    if (transitions.some((transition) => transition.phase === phase && transition.index === index)) {
      return
    }
    transitions.push({
      phase,
      index,
      knownAtIndex: index,
      price: transitionPrice(bars[index], direction, phase, boundaryPrice),
    })
  }

  function breakoutStructureLabel(swings, index, direction) {
    const localTrend = determineTrend(swings.filter((point) => point.confirmedIndex <= index)).trend
    const continuesTrend =
      (localTrend === 'up' && direction === 'bullish') || (localTrend === 'down' && direction === 'bearish')
    return localTrend === 'range' || continuesTrend ? 'BOS' : 'CHoCH'
  }

  function breakoutScore(event, closeClassification, closeDistance, atr) {
    let score = 0.25 + Math.min(1, closeDistance / Math.max(atr, DEFAULT_TICK_SIZE)) * 0.2
    if (closeClassification?.isTrendBar) score += 0.2
    if (event.followThrough === 'strong') score += 0.2
    if (event.followThrough === 'weak') score += 0.1
    if (event.retestIndex !== null) score += 0.05
    if (event.continuationIndex !== null) score += 0.1
    if (event.failedIndex !== null) score -= 0.35
    return clamp01(score)
  }

  function findBreakoutAnalysis(bars, swings, barClassifications = classifyPriceBars(bars)) {
    const breakouts = []
    const structures = []
    const sortedSwings = swings
      .slice()
      .sort((left, right) => left.confirmedIndex - right.confirmedIndex || left.index - right.index)

    sortedSwings.forEach((boundary, boundaryOrder) => {
      const direction = boundary.type === 'high' ? 'bullish' : 'bearish'
      const boundaryType = boundary.type === 'high' ? 'swing-high' : 'swing-low'
      const brokenObjectId = `${boundaryType}:${boundary.index}`
      const nextSameType = sortedSwings.slice(boundaryOrder + 1).find((point) => point.type === boundary.type)
      const activeUntilIndex = Math.min(
        bars.length - 1,
        nextSameType ? nextSameType.confirmedIndex - 1 : bars.length - 1,
      )
      let index = boundary.confirmedIndex + 1
      let attempt = 0

      while (index <= activeUntilIndex) {
        const tolerance = breakoutTolerance(bars, index, boundary.price)
        if (!intrabarBreaksBoundary(bars[index], direction, boundary.price, tolerance)) {
          index += 1
          continue
        }

        attempt += 1
        const id = `breakout:${brokenObjectId}:${attempt}`
        const transitions = []
        appendBreakoutTransition(transitions, 'intrabar-break', index, bars, direction, boundary.price)
        const closeBreak = closesBeyondBoundary(bars[index], direction, boundary.price, tolerance)
        if (!closeBreak) {
          appendBreakoutTransition(transitions, 'failed-breakout', index, bars, direction, boundary.price)
          breakouts.push({
            id,
            brokenObjectId,
            boundaryType,
            boundaryIndex: boundary.index,
            boundaryPrice: boundary.price,
            boundaryKnownAtIndex: boundary.confirmedIndex,
            direction,
            structureLabel: null,
            phase: 'failed-breakout',
            startedAtIndex: index,
            knownAtIndex: index,
            closeBreakIndex: null,
            followThroughIndex: null,
            retestIndex: null,
            continuationIndex: null,
            failedIndex: index,
            followThrough: 'failed',
            score: 0.05,
            evidence: ['盘中越过具体摆动边界'],
            warnings: ['收盘重新回到原边界内'],
            transitions,
            provenance: 'book-concept',
          })
          index += 1
          continue
        }

        const closeBreakIndex = index
        const structureLabel = breakoutStructureLabel(swings, closeBreakIndex, direction)
        appendBreakoutTransition(transitions, 'close-break', index, bars, direction, boundary.price)
        appendBreakoutTransition(
          transitions,
          'awaiting-follow-through',
          index,
          bars,
          direction,
          boundary.price,
        )
        let phase = 'awaiting-follow-through'
        let followThrough = 'none'
        let followThroughIndex = null
        let retestIndex = null
        let continuationIndex = null
        let failedIndex = null
        let lastProcessedIndex = index
        let bestClose = bars[index].close
        const followThroughLimit = Math.min(activeUntilIndex, index + BREAKOUT_FOLLOW_THROUGH_WINDOW)
        const monitorLimit = Math.min(activeUntilIndex, index + BREAKOUT_MONITOR_WINDOW)

        for (let candidateIndex = index + 1; candidateIndex <= followThroughLimit; candidateIndex += 1) {
          lastProcessedIndex = candidateIndex
          const candidateTolerance = breakoutTolerance(bars, candidateIndex, boundary.price)
          if (closesInsideOldRange(bars[candidateIndex], direction, boundary.price, candidateTolerance)) {
            failedIndex = candidateIndex
            followThrough = 'failed'
            phase = 'failed-breakout'
            appendBreakoutTransition(transitions, phase, candidateIndex, bars, direction, boundary.price)
            break
          }
          const extended =
            direction === 'bullish'
              ? bars[candidateIndex].close > bestClose + candidateTolerance
              : bars[candidateIndex].close < bestClose - candidateTolerance
          const followThroughWindowComplete =
            candidateIndex === closeBreakIndex + BREAKOUT_FOLLOW_THROUGH_WINDOW
          if (extended || followThroughWindowComplete) {
            const classification = barClassifications[candidateIndex]
            const strong = Boolean(
              classification && classification.isTrendBar && classification.direction === direction,
            )
            followThrough = strong ? 'strong' : 'weak'
            followThroughIndex = candidateIndex
            phase = 'confirmed-breakout'
            appendBreakoutTransition(transitions, phase, candidateIndex, bars, direction, boundary.price)
            bestClose =
              direction === 'bullish'
                ? Math.max(bestClose, bars[candidateIndex].close)
                : Math.min(bestClose, bars[candidateIndex].close)
            break
          }
        }

        if (phase === 'confirmed-breakout') {
          for (
            let candidateIndex = (followThroughIndex || closeBreakIndex) + 1;
            candidateIndex <= monitorLimit;
            candidateIndex += 1
          ) {
            lastProcessedIndex = candidateIndex
            const candidate = bars[candidateIndex]
            const candidateTolerance = breakoutTolerance(bars, candidateIndex, boundary.price)
            if (closesInsideOldRange(candidate, direction, boundary.price, candidateTolerance)) {
              failedIndex = candidateIndex
              phase = 'failed-breakout'
              appendBreakoutTransition(transitions, phase, candidateIndex, bars, direction, boundary.price)
              break
            }
            const testsBoundary =
              direction === 'bullish'
                ? candidate.low <= boundary.price + candidateTolerance && candidate.close >= boundary.price
                : candidate.high >= boundary.price - candidateTolerance && candidate.close <= boundary.price
            if (testsBoundary && retestIndex === null) {
              retestIndex = candidateIndex
              phase = 'breakout-retest'
              appendBreakoutTransition(transitions, phase, candidateIndex, bars, direction, boundary.price)
            }
            const extendsBest =
              direction === 'bullish'
                ? candidate.close > bestClose + candidateTolerance
                : candidate.close < bestClose - candidateTolerance
            if (extendsBest) {
              bestClose = candidate.close
              const classification = barClassifications[candidateIndex]
              const directionalBar = classification && classification.direction === direction
              if (
                directionalBar &&
                (retestIndex !== null || candidateIndex > (followThroughIndex || closeBreakIndex) + 1)
              ) {
                continuationIndex = candidateIndex
                phase = 'continuation'
                appendBreakoutTransition(transitions, phase, candidateIndex, bars, direction, boundary.price)
                break
              }
            }
          }
        }

        const closeDistance = Math.abs(bars[closeBreakIndex].close - boundary.price)
        const atr = calculateAtr(bars.slice(0, closeBreakIndex + 1))
        const closeClassification = barClassifications[closeBreakIndex]
        const evidence = ['盘中越过具体摆动边界', '收盘位于边界外侧']
        const warnings = []
        if (closeClassification?.isTrendBar) evidence.push('突破 K 线属于趋势 K 线')
        if (followThrough === 'strong') evidence.push('后续出现强同向跟进')
        if (followThrough === 'weak') evidence.push('后续仅出现弱跟进')
        if (retestIndex !== null) evidence.push('回测守住原突破边界')
        if (continuationIndex !== null) evidence.push('回测或确认后继续扩张')
        if (followThrough === 'none') warnings.push('尚未获得跟进')
        if (failedIndex !== null) warnings.push('收盘重新进入原区间，突破失败')
        const score = breakoutScore(
          { followThrough, retestIndex, continuationIndex, failedIndex },
          closeClassification,
          closeDistance,
          atr,
        )
        const knownAtIndex = transitions[transitions.length - 1].knownAtIndex
        breakouts.push({
          id,
          brokenObjectId,
          boundaryType,
          boundaryIndex: boundary.index,
          boundaryPrice: boundary.price,
          boundaryKnownAtIndex: boundary.confirmedIndex,
          direction,
          structureLabel,
          phase,
          startedAtIndex: closeBreakIndex,
          knownAtIndex,
          closeBreakIndex,
          followThroughIndex,
          retestIndex,
          continuationIndex,
          failedIndex,
          followThrough,
          score,
          evidence,
          warnings,
          transitions,
          provenance: 'book-concept',
        })
        structures.push({
          index: closeBreakIndex,
          knownAtIndex: closeBreakIndex,
          price: bars[closeBreakIndex].close,
          brokenIndex: boundary.index,
          brokenPrice: boundary.price,
          brokenObjectId,
          breakoutId: id,
          direction,
          label: structureLabel,
        })

        if (phase === 'failed-breakout') {
          index = Math.max(index + 1, (failedIndex || lastProcessedIndex) + 1)
          continue
        }
        break
      }
    })

    return {
      breakouts: breakouts.sort(
        (left, right) =>
          left.startedAtIndex - right.startedAtIndex || left.boundaryIndex - right.boundaryIndex,
      ),
      structures: structures.sort(
        (left, right) => left.index - right.index || left.brokenIndex - right.brokenIndex,
      ),
    }
  }

  function candleDirection(bar) {
    const comparison = comparePrices(bar.close, bar.open)
    return comparison > 0 ? 'bullish' : comparison < 0 ? 'bearish' : 'neutral'
  }

  function signalStatus(bars, index, direction) {
    const signalBar = bars[index]
    const barsSince = bars.length - index - 1
    const confirmationBars = bars.slice(index + 1, index + 1 + SIGNAL_CONFIRMATION_WINDOW)
    for (const bar of confirmationBars) {
      if (direction === 'bullish') {
        if (bar.close > signalBar.high) {
          return { status: 'confirmed', statusLabel: '已确认', barsSince }
        }
        if (bar.close < signalBar.low) {
          return { status: 'failed', statusLabel: '已失效', barsSince }
        }
      } else if (direction === 'bearish') {
        if (bar.close < signalBar.low) {
          return { status: 'confirmed', statusLabel: '已确认', barsSince }
        }
        if (bar.close > signalBar.high) {
          return { status: 'failed', statusLabel: '已失效', barsSince }
        }
      } else if (bar.close > signalBar.high || bar.close < signalBar.low) {
        return { status: 'confirmed', statusLabel: '区间已突破', barsSince }
      }
    }
    if (barsSince >= SIGNAL_CONFIRMATION_WINDOW) {
      return { status: 'expired', statusLabel: '已过期', barsSince }
    }
    return { status: 'pending', statusLabel: '待确认', barsSince }
  }

  function signalContext(bars, signal, swings) {
    const bar = bars[signal.index]
    const confirmedSwings = swings.filter((point) => point.confirmedIndex <= signal.index)
    const localTrend = determineTrend(confirmedSwings).trend
    const localBars = bars.slice(0, signal.index + 1)
    const localAtr = calculateAtr(localBars)
    const localLevels = clusterLevels(confirmedSwings, bar.close, localAtr)
    const proximity = Math.max(localAtr * 0.5, bar.close * 0.003)
    const nearSupport = localLevels.some(
      (level) => level.type === 'support' && Math.abs(level.price - bar.close) <= proximity,
    )
    const nearResistance = localLevels.some(
      (level) => level.type === 'resistance' && Math.abs(level.price - bar.close) <= proximity,
    )
    const aligned =
      (signal.direction === 'bullish' && localTrend === 'up') ||
      (signal.direction === 'bearish' && localTrend === 'down')
    const atRelevantLevel =
      (signal.direction === 'bullish' && nearSupport) ||
      (signal.direction === 'bearish' && nearResistance) ||
      (signal.direction === 'neutral' && (nearSupport || nearResistance))
    if (aligned && atRelevantLevel) {
      return '顺势·关键位'
    }
    if (atRelevantLevel) {
      return '关键位附近'
    }
    if (aligned) {
      return '顺势'
    }
    if (localTrend !== 'range' && signal.direction !== 'neutral') {
      return '逆势观察'
    }
    return '一般位置'
  }

  function createSignal(bars, swings, index, type, label, direction) {
    const status = signalStatus(bars, index, direction)
    return {
      index,
      type,
      label,
      direction,
      ...status,
      contextLabel: signalContext(bars, { index, direction }, swings),
    }
  }

  function findCandleSignals(bars, swings) {
    const signals = []
    const start = Math.max(1, bars.length - 45)
    for (let index = start; index < bars.length; index += 1) {
      const bar = bars[index]
      const previous = bars[index - 1]
      const range = bar.high - bar.low
      const body = Math.abs(bar.close - bar.open)
      const upperWick = bar.high - Math.max(bar.open, bar.close)
      const lowerWick = Math.min(bar.open, bar.close) - bar.low
      const dominantWick = Math.max(upperWick, lowerWick)
      const opposingWick = Math.min(upperWick, lowerWick)
      if (
        range > 0 &&
        body / range <= 0.35 &&
        dominantWick >= Math.max(body * 2, range * 0.55) &&
        dominantWick >= opposingWick * 1.5
      ) {
        const direction = lowerWick > upperWick ? 'bullish' : 'bearish'
        signals.push(
          createSignal(
            bars,
            swings,
            index,
            'pin-bar',
            direction === 'bullish' ? '看涨 Pin Bar' : '看跌 Pin Bar',
            direction,
          ),
        )
      }
      if (bar.high < previous.high && bar.low > previous.low) {
        signals.push(createSignal(bars, swings, index, 'inside-bar', 'Inside Bar', 'neutral'))
      }
      const previousDirection = candleDirection(previous)
      const direction = candleDirection(bar)
      const previousBodyHigh = Math.max(previous.open, previous.close)
      const previousBodyLow = Math.min(previous.open, previous.close)
      const bodyHigh = Math.max(bar.open, bar.close)
      const bodyLow = Math.min(bar.open, bar.close)
      const oppositeBodies =
        (direction === 'bullish' && previousDirection === 'bearish') ||
        (direction === 'bearish' && previousDirection === 'bullish')
      if (oppositeBodies && bodyHigh >= previousBodyHigh && bodyLow <= previousBodyLow) {
        signals.push(
          createSignal(
            bars,
            swings,
            index,
            'engulfing',
            direction === 'bullish' ? '看涨吞没' : '看跌吞没',
            direction,
          ),
        )
      }
      if (bar.high > previous.high && bar.low < previous.low) {
        signals.push(createSignal(bars, swings, index, 'outside-bar', 'Outside Bar', direction))
      }
    }
    return signals.slice(-10)
  }

  function formatPrice(value) {
    if (value >= 1000) {
      return value.toFixed(1)
    }
    if (value >= 10) {
      return value.toFixed(2)
    }
    return value.toFixed(3)
  }

  function buildFindings(
    trend,
    levels,
    structures,
    breakouts,
    signals,
    channelEvents,
    microChannels,
    microChannelBreaks,
    rangePosition,
  ) {
    const findings = []
    if (trend === 'up') {
      findings.push('市场结构仍由更高高点与更高低点主导。')
    } else if (trend === 'down') {
      findings.push('市场结构仍由更低高点与更低低点主导。')
    } else {
      findings.push('当前属震荡/转换区，等待价格对边界做有效突破。')
    }
    const nearestSupport = levels
      .filter((level) => level.type === 'support')
      .sort((left, right) => right.price - left.price)[0]
    const nearestResistance = levels
      .filter((level) => level.type === 'resistance')
      .sort((left, right) => left.price - right.price)[0]
    if (nearestSupport && nearestResistance) {
      findings.push(
        `近端结构边界为 ${formatPrice(nearestSupport.price)} 支撑 / ${formatPrice(nearestResistance.price)} 压力。`,
      )
    } else if (nearestSupport) {
      findings.push(`最近有效支撑参考 ${formatPrice(nearestSupport.price)}。`)
    } else if (nearestResistance) {
      findings.push(`最近有效压力参考 ${formatPrice(nearestResistance.price)}。`)
    }
    const latestBreakout = breakouts.slice().sort((left, right) => right.knownAtIndex - left.knownAtIndex)[0]
    if (latestBreakout) {
      const phaseText = {
        'intrabar-break': '盘中越过',
        'close-break': '收盘突破',
        'awaiting-follow-through': '等待跟进',
        'confirmed-breakout': '已获跟进',
        'breakout-retest': '正在回测边界',
        continuation: '回测后延续',
        'failed-breakout': '已失败并回到原区间',
      }
      findings.push(
        `最近突破：${latestBreakout.direction === 'bullish' ? '向上' : '向下'}${phaseText[latestBreakout.phase]}。`,
      )
    } else {
      const lastStructure = structures[structures.length - 1]
      if (lastStructure) {
        findings.push(
          `最近结构事件：${lastStructure.direction === 'bullish' ? '向上' : '向下'} ${lastStructure.label}。`,
        )
      }
    }
    const latestMicroChannel = microChannels
      .slice()
      .sort((left, right) => right.lastUpdatedIndex - left.lastUpdatedIndex || right.length - left.length)[0]
    const latestMicroChannelBreak = microChannelBreaks[microChannelBreaks.length - 1]
    if (
      latestMicroChannel &&
      (!latestMicroChannelBreak || latestMicroChannel.lastUpdatedIndex >= latestMicroChannelBreak.index)
    ) {
      findings.push(`当前${latestMicroChannel.label}已延续 ${latestMicroChannel.length} 根 K 线。`)
    } else if (latestMicroChannelBreak) {
      const label = latestMicroChannelBreak.channelDirection === 'bullish' ? '牛微型通道' : '熊微型通道'
      findings.push(`${label}最近出现首次反向突破，先按小回调观察。`)
    }
    const latestChannelEvent = channelEvents[channelEvents.length - 1]
    if (latestChannelEvent) {
      const channelEventText = {
        touch: '价格测试趋势通道边界',
        overshoot: '价格超越趋势通道，注意加速或高潮',
        undershoot: '推进未及趋势通道，动能可能减弱',
        'trend-line-break': '价格收盘突破趋势线，原趋势结构减弱',
        'break-and-test': '趋势线突破后重新测试原推进极值',
      }
      findings.push(`最近通道事件：${channelEventText[latestChannelEvent.type]}。`)
    }
    const recentSignal = signals
      .slice()
      .reverse()
      .find((signal) => signal.barsSince <= SIGNAL_CONFIRMATION_WINDOW)
    if (recentSignal) {
      findings.push(
        `最近 K 线形态：${recentSignal.label}，${recentSignal.contextLabel}，${recentSignal.statusLabel}。`,
      )
    }
    if (rangePosition >= 0.8) {
      findings.push(
        trend === 'up'
          ? '当前位于近期区间高位，趋势延续与假突破需要结合后续收盘区分。'
          : '当前位于近期区间高位，需关注假突破与获利回吐。',
      )
    } else if (rangePosition <= 0.2) {
      findings.push(
        trend === 'down'
          ? '当前位于近期区间低位，下跌延续与止跌拒绝需要结合后续收盘区分。'
          : '当前位于近期区间低位，需关注破位延续或拒绝下跌。',
      )
    }
    return findings.slice(0, 5)
  }

  function buildInvalidation(trend, swings, levels, currentPrice) {
    const nearestSupport = levels
      .filter((level) => level.type === 'support')
      .sort((left, right) => right.price - left.price)[0]
    const nearestResistance = levels
      .filter((level) => level.type === 'resistance')
      .sort((left, right) => left.price - right.price)[0]
    if (trend === 'up') {
      const protectedLow = swings.filter((point) => point.type === 'low' && point.label === 'HL').slice(-1)[0]
      const reference = protectedLow ? protectedLow.price : nearestSupport?.price
      if (reference === undefined) {
        return {
          text: '当前尚无已确认的结构低点，需等待新的 HL 形成。',
          price: null,
          state: 'unavailable',
        }
      }
      const breached = comparePrices(currentPrice, reference) < 0
      return {
        text: breached
          ? `现价已收盘跌破 ${formatPrice(reference)}，当前多头结构正在失效，需重新评估。`
          : `若收盘有效跌破 ${formatPrice(reference)}，当前多头结构需重新评估。`,
        price: reference,
        state: breached ? 'breached' : 'watching',
      }
    }
    if (trend === 'down') {
      const protectedHigh = swings
        .filter((point) => point.type === 'high' && point.label === 'LH')
        .slice(-1)[0]
      const reference = protectedHigh ? protectedHigh.price : nearestResistance?.price
      if (reference === undefined) {
        return {
          text: '当前尚无已确认的结构高点，需等待新的 LH 形成。',
          price: null,
          state: 'unavailable',
        }
      }
      const breached = comparePrices(currentPrice, reference) > 0
      return {
        text: breached
          ? `现价已收盘站上 ${formatPrice(reference)}，当前空头结构正在失效，需重新评估。`
          : `若收盘有效站上 ${formatPrice(reference)}，当前空头结构需重新评估。`,
        price: reference,
        state: breached ? 'breached' : 'watching',
      }
    }
    if (nearestSupport && nearestResistance) {
      return {
        text: `等待收盘有效站上 ${formatPrice(nearestResistance.price)} 或跌破 ${formatPrice(nearestSupport.price)}，再确认新的方向。`,
        price: null,
        state: 'unavailable',
      }
    }
    return {
      text: '需等待价格收盘突破近端结构边界，才能确认新的方向。',
      price: null,
      state: 'unavailable',
    }
  }

  function summarizeHigherTimeframe(analysis, period, periodLabel, sourceDate) {
    return {
      period,
      periodLabel,
      available: true,
      sourceDate,
      trend: analysis.trend,
      trendLabel: analysis.trendLabel,
      marketStateLabel: analysis.marketState.label,
      alwaysInLabel: analysis.alwaysIn.label,
      ema20Value: analysis.ema20.latestValue,
      levels: analysis.levels.slice(0, 6).map((level) => ({
        price: level.price,
        type: level.type,
        label: level.label,
        strength: level.strength,
      })),
      warning: null,
      provenance: 'book-concept',
    }
  }

  function unavailableHigherTimeframe(period, periodLabel, warning = '高周期背景不可用') {
    return {
      period,
      periodLabel,
      available: false,
      sourceDate: null,
      trend: null,
      trendLabel: '不可用',
      marketStateLabel: '不可用',
      alwaysInLabel: '不可用',
      ema20Value: null,
      levels: [],
      warning,
      provenance: 'book-concept',
    }
  }

  function calculateCompleteness(swings, levels, trendResult) {
    const highs = swings.filter((point) => point.type === 'high')
    const lows = swings.filter((point) => point.type === 'low')
    const sufficientStructure = highs.length >= 3 && lows.length >= 3
    const directionalCoherence =
      trendResult.trend === 'range'
        ? Math.abs(trendResult.score) <= 1
        : Math.abs(trendResult.score) >= 3 && trendResult.highScore * trendResult.lowScore > 0
    const withoutLatestPair = swings.filter(
      (point) => point !== highs[highs.length - 1] && point !== lows[lows.length - 1],
    )
    const stableWithoutLatestPair =
      withoutLatestPair.length >= 4 && determineTrend(withoutLatestPair).trend === trendResult.trend
    const reliableLevel = levels.some((level) => level.touches >= 2)
    const evidenceCount =
      Number(sufficientStructure) +
      Number(directionalCoherence) +
      Number(stableWithoutLatestPair) +
      Number(reliableLevel)
    return evidenceCount >= 4 ? 'high' : evidenceCount >= 2 ? 'medium' : 'low'
  }

  function normalizedObjectScore(score, scale = 1) {
    if (!Number.isFinite(score)) return 0
    return Math.max(0, Math.min(1, score / scale))
  }

  function compositeObjectStatus(status) {
    if (status === 'failed') return 'failed'
    if (status === 'candidate') return 'candidate'
    return 'confirmed'
  }

  function buildAnalysisObjects(sources) {
    const lineObjects = [...sources.trendLines, ...sources.channelLines].map((line) => {
      const invalidated = line.validUntilIndex !== null
      return {
        id: `${line.kind}:${line.direction}:${line.startIndex}:${line.anchorIndex}:${line.rank}`,
        kind: line.kind,
        title: line.label,
        startIndex: line.startIndex,
        endIndex: line.endIndex,
        knownAtIndex: Math.max(line.knownAtIndex, line.validUntilIndex ?? line.knownAtIndex),
        status: invalidated ? 'failed' : 'confirmed',
        direction: line.direction === 'ascending' ? 'bullish' : 'bearish',
        score: normalizedObjectScore(line.score, 16),
        provenance: 'implementation-proxy',
        anchors: [
          { index: line.startIndex, price: line.startPrice, role: '起点' },
          { index: line.anchorIndex, price: line.anchorPrice, role: '确认锚点' },
          { index: line.endIndex, price: line.endPrice, role: invalidated ? '失效点' : '投影终点' },
        ],
        evidence: [`${line.touches} 次价格触及`, `${line.rank === 'primary' ? '主要' : '次要'}候选线`],
        warnings: invalidated ? [`第 ${line.validUntilIndex + 1} 根 K 线突破该线`] : [],
      }
    })

    const breakoutObjects = sources.breakouts.map((breakout) => ({
      id: `breakout:${breakout.id}`,
      kind: 'breakout',
      title: `${breakout.structureLabel || '边界'} ${breakout.direction === 'bullish' ? '向上突破' : '向下突破'}`,
      startIndex: breakout.boundaryIndex,
      endIndex:
        breakout.failedIndex ??
        breakout.continuationIndex ??
        breakout.retestIndex ??
        breakout.followThroughIndex ??
        breakout.closeBreakIndex ??
        breakout.startedAtIndex,
      knownAtIndex: breakout.knownAtIndex,
      status:
        breakout.phase === 'failed-breakout'
          ? 'failed'
          : ['confirmed-breakout', 'breakout-retest', 'continuation'].includes(breakout.phase)
            ? 'confirmed'
            : 'candidate',
      direction: breakout.direction,
      score: normalizedObjectScore(breakout.score),
      provenance: breakout.provenance,
      anchors: [
        { index: breakout.boundaryIndex, price: breakout.boundaryPrice, role: '被突破边界' },
        ...(breakout.closeBreakIndex === null
          ? []
          : [
              {
                index: breakout.closeBreakIndex,
                price:
                  breakout.transitions.find((transition) => transition.phase === 'close-break')?.price ??
                  breakout.boundaryPrice,
                role: '收盘突破',
              },
            ]),
      ],
      evidence: breakout.evidence,
      warnings: breakout.warnings,
      invalidationPrice: breakout.boundaryPrice,
    }))

    const gapObjects = sources.gaps.map((gap) => ({
      id: `gap:${gap.id}`,
      kind: gap.kind,
      title: gap.label,
      startIndex: gap.startIndex,
      endIndex: gap.filledIndex ?? gap.partialFillIndex ?? gap.startIndex,
      knownAtIndex: gap.statusKnownAtIndex,
      status: gap.status === 'invalidated' ? 'failed' : gap.status === 'filled' ? 'expired' : 'confirmed',
      direction: gap.direction,
      score: gap.status === 'active' ? 0.8 : 0.65,
      provenance: gap.provenance,
      anchors: [
        { index: gap.startIndex, price: gap.lowerPrice, role: '缺口下沿' },
        { index: gap.startIndex, price: gap.upperPrice, role: '缺口上沿' },
      ],
      evidence: gap.evidence,
      warnings: gap.warnings,
      invalidationPrice: gap.direction === 'bullish' ? gap.lowerPrice : gap.upperPrice,
    }))

    const rangeObjects = sources.tradingRanges.map((range) => ({
      id: `range:${range.id}`,
      kind: range.kind,
      title: range.label,
      startIndex: range.startIndex,
      endIndex: range.failedBreakoutIndex ?? range.breakoutIndex ?? range.endIndex,
      knownAtIndex: range.statusKnownAtIndex,
      status:
        range.status === 'candidate' ? 'candidate' : range.status === 'broken' ? 'expired' : 'confirmed',
      direction: range.breakoutDirection ?? 'neutral',
      score: normalizedObjectScore(range.score),
      provenance: range.provenance,
      anchors: [
        { index: range.startIndex, price: range.upperStartPrice, role: '上边界起点' },
        { index: range.endIndex, price: range.upperEndPrice, role: '上边界终点' },
        { index: range.startIndex, price: range.lowerStartPrice, role: '下边界起点' },
        { index: range.endIndex, price: range.lowerEndPrice, role: '下边界终点' },
      ],
      evidence: range.evidence,
      warnings: range.warnings,
      invalidationPrice:
        range.breakoutDirection === 'bullish'
          ? range.low
          : range.breakoutDirection === 'bearish'
            ? range.high
            : undefined,
    }))

    const measuredMoveObjects = sources.measuredMoves.map((move) => ({
      id: `measured-move:${move.id}`,
      kind: `measured-move-${move.basis}`,
      title: `${move.basis === 'pattern' ? '形态' : move.basis === 'range' ? '区间' : move.basis === 'gap' ? '缺口' : '价格腿'}测量目标`,
      startIndex: move.basisStartIndex,
      endIndex: move.reachedIndex ?? move.invalidatedIndex ?? move.projectionIndex,
      knownAtIndex: move.statusKnownAtIndex,
      status: move.status === 'invalidated' ? 'failed' : 'confirmed',
      direction: move.direction === 'up' ? 'bullish' : 'bearish',
      score: move.status === 'reached' ? 1 : 0.75,
      provenance: move.provenance,
      anchors: [
        { index: move.basisStartIndex, price: move.basisStartPrice, role: '测量起点' },
        { index: move.basisEndIndex, price: move.basisEndPrice, role: '测量终点' },
        { index: move.projectionIndex, price: move.projectionPrice, role: '投影起点' },
        { index: move.projectionIndex, price: move.targetPrice, role: '目标价' },
      ],
      evidence: move.evidence,
      warnings: move.warnings,
      invalidationPrice: move.projectionPrice,
    }))

    const patternObjects = [
      ...sources.trendPatterns.map((pattern) => ({
        id: `trend-pattern:${pattern.id}`,
        kind: pattern.kind,
        title: pattern.label,
        startIndex: pattern.startIndex,
        endIndex: pattern.endIndex,
        knownAtIndex: pattern.statusKnownAtIndex,
        status: compositeObjectStatus(pattern.status),
        direction: pattern.direction,
        score: normalizedObjectScore(pattern.score),
        provenance: pattern.provenance,
        anchors: pattern.anchors,
        evidence: pattern.evidence,
        warnings: pattern.warnings,
      })),
      ...sources.reversalPatterns.map((pattern) => ({
        id: `reversal-pattern:${pattern.id}`,
        kind: pattern.kind,
        title: pattern.label,
        startIndex: pattern.startIndex,
        endIndex: pattern.endIndex,
        knownAtIndex: pattern.statusKnownAtIndex,
        status: compositeObjectStatus(pattern.status),
        direction: pattern.direction,
        score: normalizedObjectScore(pattern.score),
        provenance: pattern.provenance,
        anchors: pattern.anchors,
        evidence: pattern.evidence,
        warnings: pattern.warnings,
        invalidationPrice: pattern.invalidationPrice,
      })),
    ]

    const barCountObjects = sources.barCounts.map((event) => ({
      id: `bar-count:${event.id}`,
      kind: 'bar-count',
      title: `${event.label} 回调计数`,
      startIndex: event.pullbackStartIndex,
      endIndex: event.entryTriggeredIndex ?? event.index,
      knownAtIndex: event.statusKnownAtIndex,
      status:
        event.followThrough === 'failed'
          ? 'failed'
          : event.entryTriggeredIndex === null
            ? 'candidate'
            : 'confirmed',
      direction: event.direction,
      score: event.followThrough === 'strong' ? 1 : event.followThrough === 'weak' ? 0.75 : 0.55,
      provenance: event.provenance,
      anchors: [
        { index: event.signalBarIndex, price: event.entryTriggerPrice, role: '入场触发价' },
        { index: event.signalBarIndex, price: event.invalidationPrice, role: '失效价' },
      ],
      evidence: event.evidence,
      warnings: event.warnings,
      invalidationPrice: event.invalidationPrice,
    }))

    const objects = [
      ...lineObjects,
      ...breakoutObjects,
      ...gapObjects,
      ...rangeObjects,
      ...measuredMoveObjects,
      ...patternObjects,
      ...barCountObjects,
    ].sort((left, right) => left.knownAtIndex - right.knownAtIndex || left.startIndex - right.startIndex)

    const uniqueObjects = new Map()
    for (const object of objects) {
      const previous = uniqueObjects.get(object.id)
      if (!previous || object.knownAtIndex >= previous.knownAtIndex) uniqueObjects.set(object.id, object)
    }
    return Array.from(uniqueObjects.values())
  }

  function analyzePriceAction(bars, emaWarmupBars = [], options = {}) {
    const closedBars = closedBarsForAnalysis(bars)
    if (closedBars.length !== bars.length) {
      return analyzePriceAction(closedBars, closedBarsForAnalysis(emaWarmupBars), options)
    }
    if (bars.length < 20) {
      throw new Error('至少需要 20 根已收盘 K 线才能进行结构分析')
    }
    const currentPrice = bars[bars.length - 1].close
    const atr = calculateAtr(bars)
    const ema20 = analyzeEma20(bars, emaWarmupBars)
    const barClassifications = classifyPriceBars(bars)
    const swings = labelSwings(findRawSwings(bars), bars)
    const structureStartIndex = Math.max(0, bars.length - (options.structureLookback ?? bars.length))
    const recentSwings = options.structureLookback
      ? labelSwings(
          swings.filter((point) => point.index >= structureStartIndex),
          bars,
        )
      : swings
    const structuralSwings = prioritizedStructuralSwings(recentSwings)
    const legs = buildPriceLegs(bars, swings)
    const barCounts = findBarCountEvents(bars, swings)
    const trendResult = determineTrend(structuralSwings)
    const levels = clusterLevels(recentSwings, currentPrice, atr)
    const background = options.structureLookback
      ? {
          trend: determineTrend(prioritizedStructuralSwings(swings)).trend,
          levels: clusterLevels(swings, currentPrice, atr),
          startDate: bars[0].date,
          count: bars.length,
        }
      : null
    const trendLines = findTrendLines(bars, structuralSwings, trendResult.trend)
    const { channelLines, channelEvents } = findTrendChannelLines(bars, swings, trendLines)
    const { microChannels, microChannelBreaks } = findMicroChannelAnalysis(bars)
    const { breakouts, structures } = findBreakoutAnalysis(bars, swings, barClassifications)
    const gaps = findPriceGaps(bars, ema20, breakouts)
    const signals = findCandleSignals(bars, recentSwings)
    const rangeCandidates = findTradingRangeCandidates(bars, swings, barClassifications)
    const tradingRanges = findTradingRanges(bars, swings, barClassifications, rangeCandidates)
    const activeTradingRange = selectCurrentTradingRange(rangeCandidates, bars, structureStartIndex)
    const baseReversalPatterns = [
      ...findDoubleTopBottomPatterns(bars, swings),
      ...findWedgeAndExpandingPatterns(bars, swings, trendResult.trend),
    ]
      .sort((left, right) => left.knownAtIndex - right.knownAtIndex)
      .slice(-16)
    const measuredMoves = buildMeasuredMoves(bars, legs, tradingRanges, gaps, baseReversalPatterns)
    const magnets = buildMagnetLevels(bars, swings, tradingRanges, breakouts, ema20, measuredMoves)
    const directionContext = buildDirectionContext({
      bars,
      breakouts,
      ema20,
      swings: recentSwings,
      structureStartIndex,
      atr,
      tick: DEFAULT_TICK_SIZE,
    })
    const marketState = classifyMarketState(
      bars,
      trendResult.trend,
      legs,
      barClassifications,
      ema20,
      activeTradingRange ? [activeTradingRange] : [],
      structureStartIndex,
      directionContext,
    )
    const reversalPatterns = [
      ...baseReversalPatterns,
      ...findMajorTrendReversalPatterns(bars, trendLines, channelEvents, swings, breakouts, marketState),
      ...findFinalFlagPatterns(bars, tradingRanges),
      ...findClimacticReversalPatterns(bars, barClassifications),
    ]
      .sort((left, right) => left.knownAtIndex - right.knownAtIndex)
      .slice(-24)
    const trendPatterns = findTrendPatterns(bars, barClassifications, legs, marketState, ema20)
    const alwaysIn = determineAlwaysIn(directionContext)
    const recentBars = bars.slice(-60)
    const rangeHigh = Math.max(...recentBars.map((bar) => bar.high))
    const rangeLow = Math.min(...recentBars.map((bar) => bar.low))
    const rangePosition = rangeHigh === rangeLow ? 0.5 : (currentPrice - rangeLow) / (rangeHigh - rangeLow)

    const tradingRangePosition = activeTradingRange
      ? (currentPrice - activeTradingRange.low) /
        Math.max(activeTradingRange.high - activeTradingRange.low, DEFAULT_TICK_SIZE)
      : null
    const confidence = calculateCompleteness(recentSwings, levels, trendResult)
    const invalidation = buildInvalidation(trendResult.trend, recentSwings, levels, currentPrice)
    const objects = buildAnalysisObjects({
      trendLines,
      channelLines,
      breakouts,
      gaps,
      tradingRanges,
      measuredMoves,
      trendPatterns,
      reversalPatterns,
      barCounts,
    })

    return {
      structureStartIndex,
      recentSwings,
      background,
      trend: trendResult.trend,
      trendLabel:
        trendResult.trend === 'up' ? '上升结构' : trendResult.trend === 'down' ? '下降结构' : '震荡结构',
      trendDescription: trendResult.description,
      marketState,
      alwaysIn,
      confidence,
      confidenceLabel: confidence === 'high' ? '高' : confidence === 'medium' ? '中' : '低',
      atr,
      ema20,
      gaps,
      barClassifications,
      rangeHigh,
      rangeLow,
      rangePosition: Math.max(0, Math.min(1, rangePosition)),
      tradingRangePosition:
        tradingRangePosition === null ? null : Math.max(0, Math.min(1, tradingRangePosition)),
      activeTradingRange,
      tradingRanges,
      measuredMoves,
      magnets,
      trendPatterns,
      reversalPatterns,
      higherTimeframe: options.higherTimeframe ?? null,
      objects,
      swings,
      legs,
      barCounts,
      levels,
      trendLines,
      channelLines,
      channelEvents,
      microChannels,
      microChannelBreaks,
      structures,
      breakouts,
      signals,
      findings: buildFindings(
        trendResult.trend,
        levels,
        structures,
        breakouts,
        signals,
        channelEvents,
        microChannels,
        microChannelBreaks,
        rangePosition,
      ),
      invalidation: invalidation.text,
      invalidationPrice: invalidation.price,
      invalidationState: invalidation.state,
    }
  }

  return {
    EMA20_PERIOD,
    EMA20_WARMUP_BAR_COUNT,
    closedBarsForAnalysis,
    calculateExponentialMovingAverage,
    emaWarmupBarsForAnalysis,
    analyzeEma20,
    calculateAtomicBarFeatures,
    classifyPriceBars,
    findPriceGaps,
    buildPriceLegs,
    findBarCountEvents,
    classifyMarketState,
    determineAlwaysIn,
    findTradingRanges,
    buildMeasuredMoves,
    buildMagnetLevels,
    findTrendPatterns,
    findDoubleTopBottomPatterns,
    findWedgeAndExpandingPatterns,
    findMajorTrendReversalPatterns,
    findFinalFlagPatterns,
    findClimacticReversalPatterns,
    findTrendLines,
    buildTrendChannelLine,
    findTrendChannelLines,
    findMicroChannelAnalysis,
    findMicroChannels,
    findBreakoutAnalysis,
    summarizeHigherTimeframe,
    unavailableHigherTimeframe,
    analyzePriceAction,
  }
}
