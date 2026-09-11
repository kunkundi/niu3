// Current control is separate from completed swing geometry and local consolidation.
// All indices refer to the supplied, already truncated, completed-bar history.
const EMA_DIRECTION_BARS = 5
const MIN_ALIGNED_CLOSES = 3
const EMA_BUFFER_ATR_RATIO = 0.05

const roundingTolerance = (left, right) => Number.EPSILON * Math.max(1, Math.abs(left), Math.abs(right)) * 16
const exceedsBuffer = (price, reference, sign, buffer) =>
  sign * (price - reference) > buffer + roundingTolerance(price, reference)
const holdsBoundary = (price, reference, sign, tick) =>
  sign * (price - reference) >= -tick - roundingTolerance(price, reference)

// Current direction uses the latest confirmed high/low pairs, independently of
// major/minor scores that can change when older points leave the display window.
function currentSwingPremise(bars, swings, structureStartIndex, tick) {
  const latestIndex = bars.length - 1
  const confirmed = swings
    .filter(
      (point) =>
        point.index >= structureStartIndex &&
        point.index <= latestIndex &&
        Number.isInteger(point.knownAtIndex) &&
        point.knownAtIndex >= point.index &&
        point.knownAtIndex <= latestIndex &&
        Number.isFinite(point.price),
    )
    .sort((left, right) => left.index - right.index)
  const highs = confirmed.filter((point) => point.type === 'high').slice(-2)
  const lows = confirmed.filter((point) => point.type === 'low').slice(-2)
  if (highs.length < 2 || lows.length < 2) return null
  const advances = (points, sign) => exceedsBuffer(points[1].price, points[0].price, sign, tick)
  const sign =
    advances(highs, 1) && advances(lows, 1) ? 1 : advances(highs, -1) && advances(lows, -1) ? -1 : 0
  if (!sign) return null
  const invalidation = sign === 1 ? lows[1] : highs[1]
  const knownAtIndex = Math.max(...[...highs, ...lows].map((point) => point.knownAtIndex))
  // A broken premise cannot recover merely because today's close returns above it.
  if (
    !bars
      .slice(invalidation.knownAtIndex)
      .every((bar) => holdsBoundary(bar.close, invalidation.price, sign, tick))
  ) {
    return null
  }
  return { direction: sign === 1 ? 'bullish' : 'bearish', sign, invalidation, knownAtIndex, highs, lows }
}

export function buildDirectionContext({ bars, breakouts, ema20, swings, structureStartIndex, atr, tick }) {
  const latestIndex = bars.length - 1
  const close = bars.at(-1).close
  const evidence = []
  const warnings = []
  const buffer = Math.max(tick, atr * EMA_BUFFER_ATR_RATIO)
  const recent = ema20.points.filter(
    (point) => point.index > latestIndex - EMA_DIRECTION_BARS && point.index <= latestIndex,
  )
  const lastEma = recent.at(-1)?.value
  const aligned = (sign) =>
    recent.length === EMA_DIRECTION_BARS &&
    exceedsBuffer(close, lastEma, sign, buffer) &&
    exceedsBuffer(lastEma, recent[0].value, sign, buffer) &&
    recent.filter((point) => exceedsBuffer(bars[point.index].close, point.value, sign, buffer)).length >=
      MIN_ALIGNED_CLOSES
  const emaDirection = aligned(1) ? 'bullish' : aligned(-1) ? 'bearish' : null

  // Awaiting follow-through and intrabar failures cannot establish or replace control.
  // Select the latest confirmation before checking its current validity, so an old
  // successful event cannot come back merely because a newer confirmation failed.
  const confirmed = breakouts
    .filter(
      (event) =>
        event.boundaryIndex >= structureStartIndex &&
        event.boundaryIndex <= latestIndex &&
        Number.isFinite(event.boundaryPrice) &&
        Number.isInteger(event.closeBreakIndex) &&
        Number.isInteger(event.followThroughIndex) &&
        event.closeBreakIndex <= event.followThroughIndex &&
        event.followThroughIndex >= structureStartIndex &&
        event.followThroughIndex <= latestIndex &&
        event.knownAtIndex <= latestIndex,
    )
    .sort(
      (left, right) =>
        right.followThroughIndex - left.followThroughIndex ||
        right.closeBreakIndex - left.closeBreakIndex ||
        right.boundaryIndex - left.boundaryIndex,
    )
  const latest = confirmed[0]
  const conflicting =
    latest &&
    confirmed.some(
      (event) =>
        event.followThroughIndex === latest.followThroughIndex && event.direction !== latest.direction,
    )
  const sign = latest?.direction === 'bullish' ? 1 : -1
  const firstBoundaryLoss = latest
    ? bars.findIndex(
        (bar, index) =>
          index >= latest.closeBreakIndex && !holdsBoundary(bar.close, latest.boundaryPrice, sign, tick),
      )
    : -1
  const invalidations = [latest?.failedIndex, firstBoundaryLoss].filter(
    (index) => Number.isInteger(index) && index >= 0 && index <= latestIndex,
  )
  const breakoutInvalidatedAt = invalidations.length ? Math.min(...invalidations) : latest?.knownAtIndex
  // Historical breakout objects stop monitoring after a bounded period. Recheck
  // every later close here, with a fixed tick tolerance: re-entry ends this premise
  // permanently, even if price crosses the same boundary again much later.
  const intact =
    latest &&
    ['confirmed-breakout', 'breakout-retest', 'continuation'].includes(latest.phase) &&
    latest.failedIndex == null &&
    ['strong', 'weak'].includes(latest.followThrough) &&
    exceedsBuffer(close, latest.boundaryPrice, sign, tick) &&
    firstBoundaryLoss === -1

  let direction = null
  let source = 'none'
  let premiseKnownAtIndex = null
  let swingBoundaryPrice = null
  if (conflicting) {
    warnings.push('同日确认的突破方向冲突，方向暂不明确')
  } else if (intact) {
    if (emaDirection === latest.direction) {
      direction = latest.direction
      source = 'breakout'
      premiseKnownAtIndex = latest.followThroughIndex
      evidence.push(
        `${bars[latest.followThroughIndex].date} 已确认${sign === 1 ? '向上' : '向下'}突破 ${latest.boundaryPrice.toFixed(3)}，并获得${latest.followThrough === 'strong' ? '强' : '弱'}跟进`,
      )
      evidence.push('截至当前收盘，原突破边界仍守住')
    } else {
      warnings.push('已确认突破与近期价格、EMA20 尚未形成一致方向')
    }
  } else {
    const premise = currentSwingPremise(bars, swings, structureStartIndex, tick)
    // A post-failure higher low / lower high is new evidence. A pivot that formed
    // before the failure but became known later must not revive the old premise.
    const fresh = !latest || (premise && premise.invalidation.index > breakoutInvalidatedAt)
    if (premise && premise.direction === emaDirection && fresh) {
      direction = premise.direction
      source = 'swings'
      premiseKnownAtIndex = premise.knownAtIndex
      swingBoundaryPrice = premise.invalidation.price
      evidence.push(
        `最近两组已确认高点 ${premise.highs.map((point) => point.price.toFixed(3)).join(' → ')}、低点 ${premise.lows.map((point) => point.price.toFixed(3)).join(' → ')} 同步${premise.sign === 1 ? '抬高' : '降低'}`,
      )
      if (latest) {
        evidence.push(
          `${invalidations.length ? '旧突破失效后，' : ''}${bars[premise.invalidation.knownAtIndex].date} 确认的新${premise.sign === 1 ? '低点' : '高点'}重新建立方向依据`,
        )
      }
      evidence.push(`自摆动边界确认以来，收盘持续守住 ${swingBoundaryPrice.toFixed(3)}`)
    } else if (latest) {
      warnings.push('最近已确认突破不再提供方向依据；恢复方向需要新的已确认结构')
    }
  }
  if (direction) {
    evidence.push(
      `最近 5 根中至少 3 根收盘位于 EMA20 ${direction === 'bullish' ? '上' : '下'}方，当前价格与均线变化同向`,
    )
  } else {
    warnings.push('当前证据不足以支持明确的 Always-In 方向')
  }
  return {
    direction,
    trend: direction === 'bullish' ? 'up' : direction === 'bearish' ? 'down' : 'range',
    source,
    knownAtIndex: latestIndex,
    premiseKnownAtIndex,
    breakoutId: source === 'breakout' ? latest.id : null,
    boundaryPrice: source === 'breakout' ? latest.boundaryPrice : null,
    swingBoundaryPrice,
    emaDirection,
    evidence,
    warnings,
  }
}

export function summarizeDirection(context) {
  const state =
    context.direction === 'bullish' ? 'long' : context.direction === 'bearish' ? 'short' : 'unclear'
  return {
    ...context,
    state,
    label: state === 'long' ? 'Always-In 多' : state === 'short' ? 'Always-In 空' : 'Always-In 不明',
    provenance: 'implementation-proxy',
  }
}
