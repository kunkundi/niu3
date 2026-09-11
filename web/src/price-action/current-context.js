// Current readings use the same structure window as the swing trend.
// Terminal objects remain available to historical overlays, not current control.
export function selectCurrentTradingRange(ranges, bars, structureStartIndex = 0) {
  const latestIndex = bars.length - 1
  const close = bars.at(-1)?.close
  return (
    ranges
      .filter(
        (range) =>
          ['confirmed', 'breakout-mode'].includes(range.status) &&
          range.startIndex >= structureStartIndex &&
          range.knownAtIndex <= latestIndex &&
          (range.statusKnownAtIndex ?? range.knownAtIndex) <= latestIndex &&
          range.endIndex >= latestIndex &&
          // The range lifecycle checks only the next two bars for follow-through.
          (range.breakoutIndex == null || latestIndex - range.breakoutIndex <= 2) &&
          containsCurrentPrice(range, latestIndex, close),
      )
      .sort(
        (left, right) =>
          right.knownAtIndex - left.knownAtIndex ||
          right.startIndex - left.startIndex ||
          right.score - left.score,
      )[0] ?? null
  )
}

function containsCurrentPrice(range, index, close) {
  const progress = (index - range.startIndex) / Math.max(1, range.knownAtIndex - range.startIndex)
  const upper = range.upperStartPrice + (range.upperEndPrice - range.upperStartPrice) * progress
  const lower = range.lowerStartPrice + (range.lowerEndPrice - range.lowerStartPrice) * progress
  // A crossed apex is no longer compression; an already escaped new range is background.
  return upper > lower && (range.breakoutIndex != null || (close >= lower && close <= upper))
}
