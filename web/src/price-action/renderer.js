// Imported from NiuTwo/miniprogram/utils/kline-chart.ts.
// Source SHA-256: 802b88131e0d644b2f77e053bf3fe6da03782f215750ba0f788d574ef6f32bb1
// Reproduce with: node scripts/import_niutwo.mjs /path/to/NiuTwo

const COLOR_THEMES = {
  dark: {
    background: '#0b1424',
    grid: 'rgba(132, 151, 180, 0.12)',
    axis: '#71819a',
    text: '#dbe5f4',
    rise: '#d6433a',
    fall: '#3f8e27',
    majorRiseLeg: '#ff934d',
    majorFallLeg: '#3bd4c5',
    minorLeg: '#8fb3e2',
    ema: '#65a8ff',
    support: '#35c5df',
    resistance: '#f2a63b',
    trend: '#a88bfa',
    current: '#e8eef8',
    crosshair: 'rgba(232, 238, 248, 0.65)',
    tooltip: 'rgba(5, 11, 22, 0.92)',
    neutral: '#d5dae3',
  },
  light: {
    background: '#ffffff',
    grid: 'rgba(78, 89, 105, 0.12)',
    axis: '#8c95a3',
    text: '#1f2329',
    rise: '#d6433a',
    fall: '#3f8e27',
    majorRiseLeg: '#f06a24',
    majorFallLeg: '#12a594',
    minorLeg: '#5b78a6',
    ema: '#2f73c9',
    support: '#159bb3',
    resistance: '#bd7c1d',
    trend: '#7860c5',
    current: '#646b78',
    crosshair: 'rgba(78, 89, 105, 0.55)',
    tooltip: 'rgba(255, 255, 255, 0.96)',
    neutral: '#7f8794',
  },
}

const PRICE_COLOR_SCHEMES = {
  'red-green': {
    rise: '#d6433a',
    fall: '#3f8e27',
  },
  accessible: {
    rise: '#d6433a',
    fall: '#4aa1ae',
  },
}

const SIGNAL_ABBREVIATIONS = {
  'pin-bar': 'PB',
  'inside-bar': 'IB',
  engulfing: 'ENG',
  'outside-bar': 'OB',
}

const ANNOTATION_COLLISION_PADDING = 3
const TIME_AXIS_LABEL_GAP = 8
const TIME_AXIS_GRID_INTERVAL_COUNT = 4
const TIME_AXIS_LABEL_INTERVAL_COUNTS = [4, 2, 1]
const DEFAULT_TRANSITION_DURATION_MS = 320
const DEFAULT_DRAWABLE_LAYERS = {
  volume: true,
  ema20: true,
  gaps: false,
  barClassifications: false,
  tradingRanges: true,
  measuredMoves: false,
  trendPatterns: false,
  reversalPatterns: false,
  higherTimeframe: false,
  support: true,
  resistance: true,
  trendLines: true,
  trendChannels: true,
  legs: false,
  barCounts: true,
  microChannels: true,
  invalidation: true,
  bos: true,
  choch: true,
  pinBar: true,
  insideBar: true,
  engulfing: true,
  outsideBar: true,
  swings: true,
}

function signalStatusSymbol(status) {
  if (status === 'confirmed') {
    return '✓'
  }
  if (status === 'failed') {
    return '×'
  }
  if (status === 'pending') {
    return '?'
  }
  return ''
}

function signalGroupLabel(signals) {
  const statuses = new Set(signals.map((signal) => signal.status))
  const abbreviations = signals.map((signal) => SIGNAL_ABBREVIATIONS[signal.type])
  if (statuses.size === 1) {
    return `${abbreviations.join('+')}${signalStatusSymbol(signals[0].status)}`
  }
  return signals
    .map((signal) => `${SIGNAL_ABBREVIATIONS[signal.type]}${signalStatusSymbol(signal.status)}`)
    .join('+')
}

function groupSignals(signals) {
  const groups = new Map()
  signals
    .filter((signal) => signal.status !== 'expired')
    .forEach((signal) => {
      const existing = groups.get(signal.index) || []
      existing.push(signal)
      groups.set(signal.index, existing)
    })
  return Array.from(groups.entries())
    .sort((left, right) => left[0] - right[0])
    .map(([index, groupedSignals]) => ({ index, signals: groupedSignals }))
}

function lerp(from, to, progress) {
  return from + (to - from) * progress
}

function easeInOutCubic(progress) {
  return progress < 0.5 ? 4 * progress * progress * progress : 1 - Math.pow(-2 * progress + 2, 3) / 2
}

function interpolateBar(from, to, progress) {
  const open = lerp(from.open, to.open, progress)
  const close = lerp(from.close, to.close, progress)
  return {
    date: progress < 0.5 ? from.date : to.date,
    open,
    close,
    high: Math.max(open, close, lerp(from.high, to.high, progress)),
    low: Math.min(open, close, lerp(from.low, to.low, progress)),
    volume: Math.max(0, lerp(from.volume, to.volume, progress)),
    closed: to.closed,
  }
}

function resampleBars(bars, count) {
  if (bars.length === 0 || count <= 0) {
    return []
  }
  if (count === 1 || bars.length === 1) {
    return Array.from({ length: count }, () => ({ ...bars[bars.length - 1] }))
  }
  return Array.from({ length: count }, (_, index) => {
    const position = (index * (bars.length - 1)) / (count - 1)
    const startIndex = Math.floor(position)
    const endIndex = Math.min(bars.length - 1, startIndex + 1)
    return interpolateBar(bars[startIndex], bars[endIndex], position - startIndex)
  })
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

function shortDate(value) {
  if (value.length >= 16) {
    return `${value.slice(5, 10)} ${value.slice(11, 16)}`
  }
  return value.slice(5, 10)
}

function roundedRect(context, x, y, width, height, radius) {
  const boundedRadius = Math.min(radius, width / 2, height / 2)
  context.beginPath()
  context.moveTo(x + boundedRadius, y)
  context.lineTo(x + width - boundedRadius, y)
  context.quadraticCurveTo(x + width, y, x + width, y + boundedRadius)
  context.lineTo(x + width, y + height - boundedRadius)
  context.quadraticCurveTo(x + width, y + height, x + width - boundedRadius, y + height)
  context.lineTo(x + boundedRadius, y + height)
  context.quadraticCurveTo(x, y + height, x, y + height - boundedRadius)
  context.lineTo(x, y + boundedRadius)
  context.quadraticCurveTo(x, y, x + boundedRadius, y)
  context.closePath()
}

function geometry(bars, width, height, showVolume = true, visibleBarCount = bars.length) {
  const plotLeft = 8
  const plotRight = Math.max(plotLeft + 40, width - 52)
  const plotTop = 18
  const plotBottom = showVolume ? Math.max(plotTop + 100, height - 88) : Math.max(plotTop + 100, height - 28)
  const volumeTop = plotBottom + 18
  const volumeBottom = height - 22
  const normalizedBarCount = Math.max(1, Math.min(bars.length, visibleBarCount))
  const completeBarCount = Math.max(1, Math.floor(normalizedBarCount))
  const completeStartIndex = bars.length - completeBarCount
  let completeMin = Number.POSITIVE_INFINITY
  let completeMax = Number.NEGATIVE_INFINITY
  let completeVolumeMax = 1
  for (let index = completeStartIndex; index < bars.length; index += 1) {
    const bar = bars[index]
    completeMin = Math.min(completeMin, bar.low)
    completeMax = Math.max(completeMax, bar.high)
    completeVolumeMax = Math.max(completeVolumeMax, bar.volume)
  }
  const partialProgress = normalizedBarCount - completeBarCount
  const partialBar = partialProgress > 0 && completeStartIndex > 0 ? bars[completeStartIndex - 1] : null
  const rawMin = partialBar
    ? lerp(completeMin, Math.min(completeMin, partialBar.low), partialProgress)
    : completeMin
  const rawMax = partialBar
    ? lerp(completeMax, Math.max(completeMax, partialBar.high), partialProgress)
    : completeMax
  const volumeMax = partialBar
    ? lerp(completeVolumeMax, Math.max(completeVolumeMax, partialBar.volume), partialProgress)
    : completeVolumeMax
  const padding = Math.max((rawMax - rawMin) * 0.08, rawMax * 0.002)
  const barWidth = (plotRight - plotLeft) / normalizedBarCount
  return {
    width,
    height,
    plotLeft,
    plotRight,
    plotTop,
    plotBottom,
    volumeTop,
    volumeBottom,
    volumeMax,
    priceMin: rawMin - padding,
    priceMax: rawMax + padding,
    barWidth,
    barOriginX: plotRight - barWidth * bars.length,
  }
}

function priceY(value, chart) {
  const ratio = (value - chart.priceMin) / Math.max(0.000001, chart.priceMax - chart.priceMin)
  return chart.plotBottom - ratio * (chart.plotBottom - chart.plotTop)
}

function barX(index, chart) {
  return chart.barOriginX + chart.barWidth * (index + 0.5)
}

function placeAnnotationLabel(
  context,
  chart,
  occupied,
  text,
  x,
  preferredY,
  direction,
  topOffset,
  bottomOffset,
  allowOppositeDirection = false,
  maxSteps = 3,
) {
  const halfWidth = context.measureText(text).width / 2 + ANNOTATION_COLLISION_PADDING
  // Keep the full label (including boxed structure labels) inside the clipped
  // viewport. Collision checks must use the same x position as the final text.
  const inset = halfWidth + 2
  if (chart.plotRight - chart.plotLeft < inset * 2) return null
  const labelX = Math.max(chart.plotLeft + inset, Math.min(chart.plotRight - inset, x))
  const step = 12
  const offsets = [0]
  for (let index = 1; index <= maxSteps; index += 1) {
    offsets.push(direction * step * index)
  }
  if (allowOppositeDirection) {
    for (let index = 1; index <= maxSteps; index += 1) {
      offsets.push(-direction * step * index)
    }
  }
  for (const offset of offsets) {
    const y = preferredY + offset
    const bounds = {
      left: labelX - halfWidth,
      right: labelX + halfWidth,
      top: y + topOffset - ANNOTATION_COLLISION_PADDING,
      bottom: y + bottomOffset + ANNOTATION_COLLISION_PADDING,
    }
    if (bounds.top < chart.plotTop || bounds.bottom > chart.plotBottom) {
      continue
    }
    const overlaps = occupied.some(
      (item) =>
        bounds.left <= item.right &&
        bounds.right >= item.left &&
        bounds.top <= item.bottom &&
        bounds.bottom >= item.top,
    )
    if (!overlaps) {
      occupied.push(bounds)
      return { x: labelX, y }
    }
  }
  return null
}

function buildTimeAxisTicks(context, chart, bars, tickCount) {
  if (tickCount === 0) {
    const label = shortDate(bars[bars.length - 1].date)
    const x = chart.plotRight
    return [
      {
        label,
        left: x - context.measureText(label).width,
        right: x,
        textAlign: 'right',
        x,
      },
    ]
  }
  return Array.from({ length: tickCount + 1 }, (_, tick) => {
    const ratio = tick / tickCount
    const index = Math.round((bars.length - 1) * ratio)
    const x = chart.plotLeft + (chart.plotRight - chart.plotLeft) * ratio
    const label = shortDate(bars[index].date)
    const textAlign = tick === 0 ? 'left' : tick === tickCount ? 'right' : 'center'
    const labelWidth = context.measureText(label).width
    const left = textAlign === 'left' ? x : textAlign === 'right' ? x - labelWidth : x - labelWidth / 2
    const right = textAlign === 'left' ? x + labelWidth : textAlign === 'right' ? x : x + labelWidth / 2
    return { label, left, right, textAlign, x }
  })
}

function timeAxisTicks(context, chart, bars) {
  for (const tickCount of TIME_AXIS_LABEL_INTERVAL_COUNTS) {
    if (tickCount > bars.length - 1) {
      continue
    }
    const ticks = buildTimeAxisTicks(context, chart, bars, tickCount)
    const labelsFit = ticks.every(
      (tick, index) => index === 0 || tick.left - ticks[index - 1].right >= TIME_AXIS_LABEL_GAP,
    )
    if (labelsFit) {
      return ticks
    }
  }
  return buildTimeAxisTicks(context, chart, bars, 0)
}

function drawGrid(context, chart, bars, colors) {
  context.lineWidth = 1
  context.font = '11px Roboto, sans-serif'
  context.textAlign = 'left'
  context.textBaseline = 'middle'
  for (let row = 0; row <= 4; row += 1) {
    const y = chart.plotTop + ((chart.plotBottom - chart.plotTop) * row) / 4
    const price = chart.priceMax - ((chart.priceMax - chart.priceMin) * row) / 4
    context.strokeStyle = colors.grid
    context.beginPath()
    context.moveTo(chart.plotLeft, y)
    context.lineTo(chart.plotRight, y)
    context.stroke()
    context.fillStyle = colors.axis
    context.fillText(formatPrice(price), chart.plotRight + 5, y)
  }
  for (let column = 0; column <= TIME_AXIS_GRID_INTERVAL_COUNT; column += 1) {
    const x = chart.plotLeft + ((chart.plotRight - chart.plotLeft) * column) / TIME_AXIS_GRID_INTERVAL_COUNT
    context.strokeStyle = colors.grid
    context.beginPath()
    context.moveTo(x, chart.plotTop)
    context.lineTo(x, chart.volumeBottom)
    context.stroke()
  }
  timeAxisTicks(context, chart, bars).forEach((tick) => {
    context.fillStyle = colors.axis
    context.textAlign = tick.textAlign
    context.fillText(tick.label, tick.x, chart.height - 8)
  })
}

function drawEma20(context, chart, analysis, colors, barOffset, barCount) {
  const visiblePoints = analysis.ema20.points
    .filter((point) => point.index >= barOffset && point.index < barOffset + barCount)
    .map((point) => ({ ...point, localIndex: point.index - barOffset }))
  if (visiblePoints.length === 0) {
    return
  }
  context.save()
  context.beginPath()
  context.rect(
    chart.plotLeft,
    chart.plotTop,
    chart.plotRight - chart.plotLeft,
    chart.plotBottom - chart.plotTop,
  )
  context.clip()
  context.strokeStyle = colors.ema
  context.lineWidth = 1.75
  context.lineCap = 'round'
  context.lineJoin = 'round'
  context.globalAlpha = 0.94
  context.beginPath()
  let previousIndex = null
  visiblePoints.forEach((point) => {
    const x = barX(point.localIndex, chart)
    const y = priceY(point.value, chart)
    if (previousIndex === null || point.localIndex !== previousIndex + 1) {
      context.moveTo(x, y)
    } else {
      context.lineTo(x, y)
    }
    previousIndex = point.localIndex
  })
  context.stroke()
  context.restore()

  const latestPoint = visiblePoints[visiblePoints.length - 1]
  const label = `EMA20 ${formatPrice(latestPoint.value)}`
  context.save()
  context.font = 'bold 9px Roboto, sans-serif'
  const labelWidth = context.measureText(label).width + 10
  const labelHeight = 16
  const labelX = chart.plotLeft + 3
  const labelY = chart.plotTop + 3
  context.fillStyle = colors.background
  context.globalAlpha = 0.88
  roundedRect(context, labelX, labelY, labelWidth, labelHeight, 4)
  context.fill()
  context.globalAlpha = 1
  context.fillStyle = colors.ema
  context.textAlign = 'left'
  context.textBaseline = 'middle'
  context.fillText(label, labelX + 5, labelY + labelHeight / 2)
  context.restore()
}

function drawPriceLegs(context, chart, analysis, colors, barOffset, barCount) {
  analysis.legs
    .filter((leg) => leg.endIndex >= barOffset && leg.startIndex < barOffset + barCount)
    .slice(-14)
    .forEach((leg) => {
      const startX = barX(leg.startIndex - barOffset, chart)
      const endX = barX(leg.endIndex - barOffset, chart)
      const startY = priceY(leg.startPrice, chart)
      const endY = priceY(leg.endPrice, chart)
      const color =
        leg.level === 'minor'
          ? colors.minorLeg
          : leg.direction === 'bull'
            ? colors.majorRiseLeg
            : colors.majorFallLeg
      context.save()
      context.beginPath()
      context.rect(
        chart.plotLeft,
        chart.plotTop,
        chart.plotRight - chart.plotLeft,
        chart.plotBottom - chart.plotTop,
      )
      context.clip()
      context.strokeStyle = color
      context.fillStyle = color
      context.globalAlpha = 1
      context.lineWidth = 1.2
      context.setLineDash([])
      context.beginPath()
      context.moveTo(startX, startY)
      context.lineTo(endX, endY)
      context.stroke()
      context.setLineDash([])
      if (leg.level === 'major' && endX >= chart.plotLeft && endX <= chart.plotRight) {
        context.globalAlpha = 0.95
        context.beginPath()
        context.arc(endX, endY, 3.25, 0, Math.PI * 2)
        context.fill()
      }
      context.restore()
    })
}

function barClassificationAbbreviation(kind) {
  const labels = {
    'bull-trend': 'BT',
    'bear-trend': 'ST',
    'trading-range': 'TR',
    doji: 'D',
    'bull-reversal': 'BR',
    'bear-reversal': 'SR',
    other: '',
  }
  return labels[kind]
}

function drawBarClassificationMarkers(
  context,
  chart,
  analysis,
  bars,
  colors,
  barOffset,
  occupiedAnnotations,
) {
  analysis.barClassifications
    .filter(
      (classification) =>
        classification.kind !== 'other' &&
        classification.index >= barOffset &&
        classification.index < barOffset + bars.length,
    )
    .slice(-12)
    .forEach((classification) => {
      const localIndex = classification.index - barOffset
      const bar = bars[localIndex]
      const label = barClassificationAbbreviation(classification.kind)
      if (!bar || !label) {
        return
      }
      const bullish = classification.direction === 'bullish'
      const bearish = classification.direction === 'bearish'
      const side = bullish ? 1 : -1
      const x = barX(localIndex, chart)
      const rawY = priceY(bullish ? bar.low : bar.high, chart) + side * 10
      context.save()
      context.fillStyle = bullish ? colors.rise : bearish ? colors.fall : colors.neutral
      context.globalAlpha =
        classification.kind === 'doji' || classification.kind === 'trading-range' ? 0.62 : 0.82
      context.font = 'bold 7px Roboto, sans-serif'
      context.textAlign = 'center'
      context.textBaseline = 'middle'
      const position = placeAnnotationLabel(
        context,
        chart,
        occupiedAnnotations,
        label,
        x,
        rawY,
        side,
        -5,
        5,
        true,
        2,
      )
      if (position !== null) {
        context.fillText(label, position.x, position.y)
      }
      context.restore()
    })
}

function drawHigherTimeframeContext(context, chart, analysis, colors) {
  const higher = analysis.higherTimeframe
  if (!higher?.available) return
  const lines = [
    ...higher.levels.map((level) => ({
      price: level.price,
      label: `${higher.periodLabel}·${level.label}`,
      color: level.type === 'support' ? colors.support : colors.resistance,
    })),
    ...(higher.ema20Value === null
      ? []
      : [
          {
            price: higher.ema20Value,
            label: `${higher.periodLabel}·EMA20`,
            color: colors.ema,
          },
        ]),
  ]
  lines.slice(0, 7).forEach((line) => {
    if (line.price < chart.priceMin || line.price > chart.priceMax) return
    const y = priceY(line.price, chart)
    context.save()
    context.strokeStyle = line.color
    context.fillStyle = line.color
    context.globalAlpha = 0.34
    context.lineWidth = 1.25
    context.setLineDash([8, 5])
    context.beginPath()
    context.moveTo(chart.plotLeft, y)
    context.lineTo(chart.plotRight, y)
    context.stroke()
    context.setLineDash([])
    context.font = 'bold 7px Roboto, sans-serif'
    context.textAlign = 'left'
    context.textBaseline = 'bottom'
    context.fillText(line.label, chart.plotLeft + 2, y - 2)
    context.restore()
  })
}

function reversalPatternAbbreviation(kind) {
  const labels = {
    'double-top': 'DT',
    'double-bottom': 'DB',
    'double-top-pullback': 'DTP',
    'double-bottom-pullback': 'DBP',
    'wedge-top': 'WT',
    'wedge-bottom': 'WB',
    'wedge-pullback': 'WP',
    'expanding-triangle': 'ET',
    'major-trend-reversal': 'MTR',
    'final-flag': 'FF',
    'climactic-reversal': 'CR',
  }
  return labels[kind]
}

function drawReversalPatterns(context, chart, analysis, colors, barOffset, visibleBarCount) {
  const visibleEnd = barOffset + visibleBarCount - 1
  analysis.reversalPatterns
    .filter((pattern) => pattern.startIndex <= visibleEnd && pattern.endIndex >= barOffset)
    .slice(-6)
    .forEach((pattern) => {
      const anchors = pattern.anchors.filter(
        (anchor) => anchor.index >= barOffset && anchor.index <= visibleEnd,
      )
      if (anchors.length === 0) return
      const bullish = pattern.direction === 'bullish'
      context.save()
      context.strokeStyle = bullish ? colors.rise : colors.fall
      context.fillStyle = context.strokeStyle
      context.globalAlpha = pattern.status === 'failed' ? 0.3 : pattern.status === 'candidate' ? 0.5 : 0.78
      context.lineWidth = pattern.status === 'confirmed' ? 1.5 : 1
      context.setLineDash(pattern.status === 'candidate' || pattern.status === 'active' ? [4, 3] : [])
      if (anchors.length >= 2) {
        context.beginPath()
        anchors.forEach((anchor, index) => {
          const x = barX(anchor.index - barOffset, chart)
          const y = priceY(anchor.price, chart)
          if (index === 0) context.moveTo(x, y)
          else context.lineTo(x, y)
        })
        context.stroke()
      }
      context.setLineDash([])
      const last = anchors[anchors.length - 1]
      const status = pattern.status === 'confirmed' ? '✓' : pattern.status === 'failed' ? '×' : '?'
      context.font = 'bold 7px Roboto, sans-serif'
      context.textAlign = 'center'
      context.textBaseline = 'bottom'
      context.fillText(
        `${reversalPatternAbbreviation(pattern.kind)}${status}`,
        barX(last.index - barOffset, chart),
        priceY(last.price, chart) - 3,
      )
      context.restore()
    })
}

function drawTrendPatterns(context, chart, analysis, colors, barOffset, visibleBarCount) {
  const visibleEnd = barOffset + visibleBarCount - 1
  analysis.trendPatterns
    .filter((pattern) => pattern.startIndex <= visibleEnd && pattern.endIndex >= barOffset)
    .slice(-4)
    .forEach((pattern) => {
      const anchors = pattern.anchors.filter(
        (anchor) => anchor.index >= barOffset && anchor.index <= visibleEnd,
      )
      if (anchors.length < 2) return
      const bullish = pattern.direction === 'bullish'
      context.save()
      context.strokeStyle = bullish ? colors.rise : colors.fall
      context.fillStyle = context.strokeStyle
      context.globalAlpha = pattern.status === 'failed' ? 0.3 : 0.62
      context.lineWidth = pattern.kind === 'spike-and-channel' ? 1.5 : 1
      context.setLineDash(pattern.kind === 'broad-channel' ? [5, 3] : [])
      context.beginPath()
      anchors.forEach((anchor, index) => {
        const x = barX(anchor.index - barOffset, chart)
        const y = priceY(anchor.price, chart)
        if (index === 0) context.moveTo(x, y)
        else context.lineTo(x, y)
      })
      context.stroke()
      context.setLineDash([])
      const first = anchors[0]
      const label =
        pattern.kind === 'spike-and-channel' ? 'S&C' : pattern.kind === 'small-pullback-trend' ? 'SPT' : 'BC'
      context.font = 'bold 7px Roboto, sans-serif'
      context.textAlign = 'left'
      context.textBaseline = 'bottom'
      context.fillText(label, barX(first.index - barOffset, chart) + 2, priceY(first.price, chart) - 3)
      context.restore()
    })
}

function drawMeasuredMoves(context, chart, analysis, colors, barOffset, visibleBarCount) {
  const visibleEnd = barOffset + visibleBarCount - 1
  analysis.measuredMoves
    .filter(
      (move) =>
        move.knownAtIndex <= visibleEnd && (move.status === 'active' || move.statusKnownAtIndex >= barOffset),
    )
    .slice(-5)
    .forEach((move) => {
      const basisStart = move.basisStartIndex - barOffset
      const basisEnd = move.basisEndIndex - barOffset
      const projection = move.projectionIndex - barOffset
      if (projection >= visibleBarCount || Math.max(basisStart, basisEnd) < 0) return
      const basisStartX = barX(Math.max(0, basisStart), chart)
      const basisEndX = barX(Math.max(0, basisEnd), chart)
      const projectionX = barX(Math.max(0, projection), chart)
      const targetEndX = chart.plotRight
      const basisStartY = priceY(move.basisStartPrice, chart)
      const basisEndY = priceY(move.basisEndPrice, chart)
      const projectionY = priceY(move.projectionPrice, chart)
      const targetY = priceY(move.targetPrice, chart)
      context.save()
      context.strokeStyle = move.direction === 'up' ? colors.rise : colors.fall
      context.fillStyle = context.strokeStyle
      context.globalAlpha = move.status === 'invalidated' ? 0.62 : move.status === 'reached' ? 0.82 : 0.95
      context.lineWidth = move.status === 'active' ? 1.4 : 1.2
      context.setLineDash([4, 3])
      context.beginPath()
      context.moveTo(basisStartX, basisStartY)
      context.lineTo(basisEndX, basisEndY)
      context.moveTo(projectionX, projectionY)
      context.lineTo(projectionX, targetY)
      context.lineTo(targetEndX, targetY)
      context.stroke()
      context.setLineDash([])
      context.font = 'bold 9px Roboto, sans-serif'
      context.textAlign = 'right'
      context.textBaseline = 'bottom'
      const status = move.status === 'reached' ? '✓' : move.status === 'invalidated' ? '×' : ''
      context.fillText(`MM${status} ${move.targetPrice.toFixed(2)}`, targetEndX - 2, targetY - 2)
      context.restore()
    })
}

function drawMagnetLevels(context, chart, analysis, colors) {
  analysis.magnets.slice(0, 6).forEach((magnet) => {
    if (magnet.price < chart.priceMin || magnet.price > chart.priceMax) return
    const y = priceY(magnet.price, chart)
    context.save()
    context.strokeStyle = colors.neutral
    context.fillStyle = colors.neutral
    context.globalAlpha = magnet.sources.length > 1 ? 0.62 : 0.34
    context.lineWidth = 1
    context.setLineDash([2, 4])
    context.beginPath()
    context.moveTo(chart.plotLeft + (chart.plotRight - chart.plotLeft) * 0.72, y)
    context.lineTo(chart.plotRight, y)
    context.stroke()
    context.setLineDash([])
    context.font = 'bold 7px Roboto, sans-serif'
    context.textAlign = 'right'
    context.textBaseline = 'bottom'
    context.fillText(`${magnet.label} ${magnet.price.toFixed(2)}`, chart.plotRight - 2, y - 2)
    context.restore()
  })
}

function drawTradingRanges(context, chart, analysis, colors, barOffset, visibleBarCount) {
  const visibleEnd = barOffset + visibleBarCount - 1
  analysis.tradingRanges
    .filter((range) => range.startIndex <= visibleEnd && range.endIndex >= barOffset)
    .slice(-2)
    .forEach((range) => {
      const startIndex = Math.max(barOffset, range.startIndex)
      const endIndex = Math.min(visibleEnd, range.endIndex)
      const startX = barX(startIndex - barOffset, chart)
      const endX = barX(endIndex - barOffset, chart)
      const upperSlope =
        (range.upperEndPrice - range.upperStartPrice) / Math.max(1, range.knownAtIndex - range.startIndex)
      const lowerSlope =
        (range.lowerEndPrice - range.lowerStartPrice) / Math.max(1, range.knownAtIndex - range.startIndex)
      const upperAt = (index) => range.upperStartPrice + upperSlope * (index - range.startIndex)
      const lowerAt = (index) => range.lowerStartPrice + lowerSlope * (index - range.startIndex)
      const upperStartY = priceY(upperAt(startIndex), chart)
      const upperEndY = priceY(upperAt(endIndex), chart)
      const lowerStartY = priceY(lowerAt(startIndex), chart)
      const lowerEndY = priceY(lowerAt(endIndex), chart)
      context.save()
      context.fillStyle = colors.neutral
      context.globalAlpha = range.status === 'broken' ? 0.035 : 0.07
      context.beginPath()
      context.moveTo(startX, upperStartY)
      context.lineTo(endX, upperEndY)
      context.lineTo(endX, lowerEndY)
      context.lineTo(startX, lowerStartY)
      context.closePath()
      context.fill()
      context.globalAlpha = range.status === 'broken' ? 0.32 : 0.65
      context.strokeStyle = colors.neutral
      context.lineWidth = 1
      context.setLineDash(range.status === 'breakout-mode' ? [4, 3] : [])
      context.beginPath()
      context.moveTo(startX, upperStartY)
      context.lineTo(endX, upperEndY)
      context.moveTo(startX, lowerStartY)
      context.lineTo(endX, lowerEndY)
      context.stroke()
      if (range.kind !== 'triangle') {
        context.setLineDash([3, 3])
        context.globalAlpha = 0.38
        const midY = priceY(range.mid, chart)
        context.beginPath()
        context.moveTo(startX, midY)
        context.lineTo(endX, midY)
        context.stroke()
      }
      context.setLineDash([])
      context.globalAlpha = 0.72
      context.fillStyle = colors.neutral
      context.font = 'bold 7px Roboto, sans-serif'
      context.textAlign = 'left'
      context.textBaseline = 'bottom'
      context.fillText(
        range.kind === 'triangle' ? 'TRI' : range.kind === 'tight-trading-range' ? 'TTR' : 'TR',
        startX + 2,
        upperStartY - 2,
      )
      context.restore()
    })
}

function drawPriceGaps(context, chart, analysis, colors, barOffset, visibleBarCount) {
  const visibleEnd = barOffset + visibleBarCount - 1
  const visibleGaps = analysis.gaps.filter(
    (gap) =>
      gap.knownAtIndex <= visibleEnd &&
      gap.startIndex <= visibleEnd &&
      (gap.filledIndex === null || gap.filledIndex >= barOffset) &&
      (gap.kind !== 'body-gap' || gap.status !== 'filled'),
  )
  const priceGapPriority = {
    'bar-gap': 1,
    'breakout-gap': 2,
    'measuring-gap': 3,
  }
  const unfilledPriceGaps = visibleGaps.filter((gap) => gap.status !== 'filled')
  const barGapOrigins = new Set(
    unfilledPriceGaps
      .filter((gap) => gap.kind === 'bar-gap')
      .map((gap) => `${gap.startIndex}:${gap.direction}`),
  )
  const priceGapsByOrigin = new Map()
  unfilledPriceGaps
    .filter(
      (gap) =>
        priceGapPriority[gap.kind] !== undefined && barGapOrigins.has(`${gap.startIndex}:${gap.direction}`),
    )
    .forEach((gap) => {
      const key = `${gap.startIndex}:${gap.direction}`
      const existing = priceGapsByOrigin.get(key)
      if (!existing || (priceGapPriority[gap.kind] || 0) > (priceGapPriority[existing.kind] || 0)) {
        priceGapsByOrigin.set(key, gap)
      }
    })
  const priceGaps = Array.from(priceGapsByOrigin.values())
    .sort((left, right) => {
      const statusWeight = (gap) => (gap.status === 'active' ? 2 : gap.status === 'partially-filled' ? 1 : 0)
      return (
        statusWeight(right) - statusWeight(left) ||
        right.upperPrice - right.lowerPrice - (left.upperPrice - left.lowerPrice) ||
        right.startIndex - left.startIndex
      )
    })
    .slice(0, 8)
    .sort((left, right) => left.startIndex - right.startIndex)
  priceGaps.forEach((gap) => {
    const start = Math.max(0, gap.startIndex - barOffset)
    const absoluteEnd = gap.filledIndex ?? visibleEnd
    const end = Math.min(visibleBarCount - 1, absoluteEnd - barOffset)
    if (end < 0 || start >= visibleBarCount) {
      return
    }
    const left = Math.max(chart.plotLeft, barX(start, chart) - chart.barWidth * 0.5)
    const right = Math.min(chart.plotRight, barX(Math.max(start, end), chart) + chart.barWidth * 0.5)
    const top = priceY(gap.upperPrice, chart)
    const bottom = priceY(gap.lowerPrice, chart)
    context.save()
    context.fillStyle = colors.neutral
    context.globalAlpha = gap.status === 'partially-filled' ? 0.18 : 0.24
    context.fillRect(left, top, Math.max(1, right - left), Math.max(1, bottom - top))
    if (gap.kind === 'breakout-gap' || gap.kind === 'measuring-gap') {
      context.globalAlpha = 0.78
      context.fillStyle = colors.neutral
      context.font = 'bold 7px Roboto, sans-serif'
      context.textAlign = 'left'
      context.textBaseline = 'bottom'
      const label = gap.kind === 'breakout-gap' ? 'BG' : 'MG'
      context.fillText(label, left + 2, top - 2)
    }
    context.restore()
  })

  const emaGapBars = visibleGaps
    .filter((gap) => gap.kind === 'ema-gap-bar' && gap.startIndex >= barOffset)
    .sort((left, right) => left.startIndex - right.startIndex)
    .filter((gap, index, gaps) => {
      const previous = gaps[index - 1]
      return !previous || previous.direction !== gap.direction || previous.startIndex + 1 !== gap.startIndex
    })
    .slice(-4)
  emaGapBars.forEach((gap) => {
    const index = gap.startIndex - barOffset
    const x = barX(index, chart)
    const y = (priceY(gap.upperPrice, chart) + priceY(gap.lowerPrice, chart)) / 2
    context.save()
    context.globalAlpha = 0.68
    context.fillStyle = colors.neutral
    context.font = 'bold 7px Roboto, sans-serif'
    context.textAlign = 'center'
    context.textBaseline = 'middle'
    context.fillText('MA', x, y)
    context.restore()
  })

  visibleGaps
    .filter((gap) => gap.kind === 'twenty-gap-bars')
    .slice(-2)
    .forEach((gap) => {
      const index = Math.max(0, Math.min(visibleBarCount - 1, gap.knownAtIndex - barOffset))
      const x = barX(index, chart)
      const y = (priceY(gap.upperPrice, chart) + priceY(gap.lowerPrice, chart)) / 2
      context.save()
      context.globalAlpha = 0.78
      context.fillStyle = colors.neutral
      context.font = 'bold 7px Roboto, sans-serif'
      context.textAlign = 'center'
      context.textBaseline = 'middle'
      context.fillText('20GB', x, y)
      context.restore()
    })
}

function drawLevels(context, chart, analysis, colors, showSupport, showResistance) {
  analysis.levels.forEach((level) => {
    if ((level.type === 'support' && !showSupport) || (level.type === 'resistance' && !showResistance)) {
      return
    }
    const y = priceY(level.price, chart)
    const zoneTop = Math.max(chart.plotTop, priceY(level.zoneHigh, chart))
    const zoneBottom = Math.min(chart.plotBottom, priceY(level.zoneLow, chart))
    if (y < chart.plotTop || y > chart.plotBottom || zoneTop > zoneBottom) {
      return
    }
    const isSupport = level.type === 'support'
    const color = isSupport ? colors.support : colors.resistance
    context.save()
    context.fillStyle = color
    context.globalAlpha = 0.035 + level.strength * 0.025
    context.fillRect(
      chart.plotLeft,
      zoneTop,
      chart.plotRight - chart.plotLeft,
      Math.max(2, zoneBottom - zoneTop),
    )
    context.strokeStyle = color
    context.globalAlpha = 0.5 + level.strength * 0.12
    context.lineWidth = level.strength === 3 ? 2 : 1.5
    context.setLineDash(isSupport ? [12, 5] : [5, 4])
    context.beginPath()
    context.moveTo(chart.plotLeft, y)
    context.lineTo(chart.plotRight, y)
    context.stroke()
    context.setLineDash([])
    context.globalAlpha = 1
    context.fillStyle = color
    context.font = 'bold 10px Roboto, sans-serif'
    context.textAlign = 'right'
    context.fillText(`${level.label} ${formatPrice(level.price)}`, chart.plotRight - 3, y - 6)
    context.restore()
  })
}

function drawInvalidation(context, chart, analysis, colors) {
  if (analysis.invalidationPrice === null) {
    return
  }
  const y = priceY(analysis.invalidationPrice, chart)
  if (y < chart.plotTop || y > chart.plotBottom) {
    return
  }
  const breached = analysis.invalidationState === 'breached'
  const color = breached
    ? analysis.trend === 'down'
      ? colors.rise
      : colors.fall
    : analysis.trend === 'up'
      ? colors.support
      : colors.resistance
  const label = `${breached ? '已失效' : '失效'} ${formatPrice(analysis.invalidationPrice)}`
  context.save()
  context.strokeStyle = color
  context.fillStyle = color
  context.globalAlpha = breached ? 0.92 : 0.72
  context.lineWidth = breached ? 1.75 : 1.25
  context.setLineDash(breached ? [2, 2] : [7, 4])
  context.beginPath()
  context.moveTo(chart.plotLeft, y)
  context.lineTo(chart.plotRight, y)
  context.stroke()
  context.setLineDash([])
  context.globalAlpha = 1
  context.font = 'bold 9px Roboto, sans-serif'
  context.textAlign = 'left'
  context.textBaseline = 'bottom'
  context.fillText(label, chart.plotLeft + 4, y - 3)
  context.restore()
}

function drawTrendLines(context, chart, analysis, colors, barOffset, barCount, showTrendChannels) {
  if (showTrendChannels) {
    analysis.channelLines.forEach((line) => {
      if (line.endIndex < barOffset || line.startIndex >= barOffset + barCount) {
        return
      }
      const startX = barX(line.startIndex - barOffset, chart)
      const startY = priceY(line.startPrice, chart)
      const endX = barX(line.endIndex - barOffset, chart)
      const endY = priceY(line.endPrice, chart)

      context.save()
      context.beginPath()
      context.rect(
        chart.plotLeft,
        chart.plotTop,
        chart.plotRight - chart.plotLeft,
        chart.plotBottom - chart.plotTop,
      )
      context.clip()
      context.strokeStyle = colors.trend
      context.lineWidth = line.rank === 'primary' ? 1.25 : 1
      context.lineCap = 'round'
      context.globalAlpha = line.rank === 'primary' ? 0.72 : 0.42
      context.setLineDash([2, 3])
      context.beginPath()
      context.moveTo(startX, startY)
      context.lineTo(endX, endY)
      context.stroke()
      context.setLineDash([])
      context.restore()
    })
  }

  analysis.trendLines.forEach((line) => {
    if (line.endIndex < barOffset || line.startIndex >= barOffset + barCount) {
      return
    }
    const startX = barX(line.startIndex - barOffset, chart)
    const startY = priceY(line.startPrice, chart)
    const anchorX = barX(line.anchorIndex - barOffset, chart)
    const anchorY = priceY(line.anchorPrice, chart)
    const endX = barX(line.endIndex - barOffset, chart)
    const endY = priceY(line.endPrice, chart)

    context.save()
    context.beginPath()
    context.rect(
      chart.plotLeft,
      chart.plotTop,
      chart.plotRight - chart.plotLeft,
      chart.plotBottom - chart.plotTop,
    )
    context.clip()
    context.strokeStyle = colors.trend
    context.lineWidth = line.rank === 'primary' ? 2 : 1.25
    context.lineCap = 'round'
    context.globalAlpha = line.rank === 'primary' ? 0.95 : 0.55
    context.beginPath()
    context.moveTo(startX, startY)
    context.lineTo(anchorX, anchorY)
    context.stroke()

    context.globalAlpha =
      line.validUntilIndex === null
        ? line.rank === 'primary'
          ? 0.78
          : 0.42
        : line.rank === 'primary'
          ? 0.62
          : 0.34
    context.lineWidth = line.rank === 'primary' ? 1.75 : 1
    context.setLineDash(line.validUntilIndex === null ? [5, 4] : [3, 3])
    context.beginPath()
    context.moveTo(anchorX, anchorY)
    context.lineTo(endX, endY)
    context.stroke()
    context.setLineDash([])

    context.globalAlpha = 1
    context.fillStyle = colors.background
    context.strokeStyle = colors.trend
    context.lineWidth = line.rank === 'primary' ? 2 : 1.25
    const anchors = [
      { index: line.startIndex, x: startX, y: startY },
      { index: line.anchorIndex, x: anchorX, y: anchorY },
    ]
    anchors.forEach((point) => {
      if (
        point.index < barOffset ||
        point.index >= barOffset + barCount ||
        point.y < chart.plotTop ||
        point.y > chart.plotBottom
      ) {
        return
      }
      context.beginPath()
      context.arc(point.x, point.y, line.rank === 'primary' ? 3 : 2, 0, Math.PI * 2)
      context.fill()
      context.stroke()
    })
    context.restore()

    if (line.rank === 'primary' && endY >= chart.plotTop && endY <= chart.plotBottom) {
      const label = `${line.direction === 'ascending' ? '上升' : '下降'} ${formatPrice(line.endPrice)}`
      context.save()
      context.font = 'bold 10px Roboto, sans-serif'
      const labelWidth = context.measureText(label).width + 10
      const labelHeight = 16
      const labelRight = Math.min(endX - 3, chart.plotRight - 3)
      const labelTop = Math.max(
        chart.plotTop + 2,
        Math.min(chart.plotBottom - labelHeight - 2, endY - labelHeight - 5),
      )
      context.fillStyle = colors.background
      context.globalAlpha = 0.92
      roundedRect(context, labelRight - labelWidth, labelTop, labelWidth, labelHeight, 4)
      context.fill()
      context.globalAlpha = 1
      context.strokeStyle = colors.trend
      context.lineWidth = 1
      context.stroke()
      context.fillStyle = colors.trend
      context.textAlign = 'right'
      context.textBaseline = 'middle'
      context.fillText(label, labelRight - 5, labelTop + labelHeight / 2)
      context.restore()
    }
  })
}

function trendChannelEventAbbreviation(event) {
  if (event.type === 'overshoot') return 'OS'
  if (event.type === 'undershoot') return 'US'
  if (event.type === 'trend-line-break') return 'TLB'
  if (event.type === 'break-and-test') return 'B&T'
  return 'T'
}

function drawTrendChannelEvents(
  context,
  chart,
  analysis,
  bars,
  colors,
  barOffset,
  occupiedAnnotations,
  showTrendChannels,
) {
  analysis.channelEvents.forEach((event) => {
    const channelEvent = event.type === 'touch' || event.type === 'overshoot' || event.type === 'undershoot'
    if (channelEvent && !showTrendChannels) {
      return
    }
    const localIndex = event.index - barOffset
    if (localIndex < 0 || localIndex >= bars.length) {
      return
    }
    const x = barX(localIndex, chart)
    const boundaryY = priceY(event.boundaryPrice, chart)
    const priceYValue = priceY(event.price, chart)
    const advancingSideEvent = event.type !== 'trend-line-break'
    const upperSide = event.direction === 'ascending' ? advancingSideEvent : !advancingSideEvent
    const side = upperSide ? -1 : 1
    const abbreviation = trendChannelEventAbbreviation(event)

    context.save()
    context.strokeStyle = colors.trend
    context.fillStyle = colors.trend
    context.globalAlpha = event.lineRank === 'primary' ? 0.9 : 0.58
    context.lineWidth = event.type === 'trend-line-break' ? 1.5 : 1.25
    if (boundaryY >= chart.plotTop && boundaryY <= chart.plotBottom) {
      context.beginPath()
      if (event.type === 'touch') {
        context.arc(x, boundaryY, 2.25, 0, Math.PI * 2)
        context.fill()
      } else {
        context.moveTo(x - 5, boundaryY)
        context.lineTo(x + 5, boundaryY)
        context.stroke()
      }
    }
    if (event.type === 'touch') {
      context.restore()
      return
    }

    context.font = 'bold 7px Roboto, sans-serif'
    context.textAlign = 'center'
    context.textBaseline = 'middle'
    const rawY = priceYValue + side * 11
    const position = placeAnnotationLabel(
      context,
      chart,
      occupiedAnnotations,
      abbreviation,
      x,
      rawY,
      side,
      -5,
      5,
      true,
      3,
    )
    if (position !== null) {
      context.fillText(abbreviation, position.x, position.y)
    }
    context.restore()
  })
}

function drawMicroChannels(context, chart, analysis, colors, barOffset, barCount) {
  analysis.microChannels.forEach((channel) => {
    if (channel.endIndex < barOffset || channel.startIndex >= barOffset + barCount) {
      return
    }
    const startX = barX(channel.startIndex - barOffset, chart)
    const endX = barX(channel.endIndex - barOffset, chart)
    const startY = priceY(channel.startPrice, chart)
    const endY = priceY(channel.endPrice, chart)
    context.save()
    context.beginPath()
    context.rect(
      chart.plotLeft,
      chart.plotTop,
      chart.plotRight - chart.plotLeft,
      chart.plotBottom - chart.plotTop,
    )
    context.clip()
    context.strokeStyle = channel.direction === 'bullish' ? colors.rise : colors.fall
    context.lineWidth = 1
    context.lineCap = 'round'
    context.globalAlpha = 0.38
    context.beginPath()
    context.moveTo(startX, startY)
    context.lineTo(endX, endY)
    context.stroke()
    context.restore()
  })
}

function drawMicroChannelBreaks(context, chart, analysis, bars, colors, barOffset, occupiedAnnotations) {
  analysis.microChannelBreaks
    .filter((event) => event.index >= barOffset && event.index < barOffset + bars.length)
    .slice(-6)
    .forEach((event) => {
      const visibleIndex = event.index - barOffset
      const bar = bars[visibleIndex]
      const x = barX(visibleIndex, chart)
      const bullishChannel = event.channelDirection === 'bullish'
      const y = priceY(bullishChannel ? bar.low : bar.high, chart) + (bullishChannel ? 11 : -11)
      context.save()
      context.fillStyle = event.direction === 'bullish' ? colors.rise : colors.fall
      context.globalAlpha = 0.72
      context.font = 'bold 8px Roboto, sans-serif'
      context.textAlign = 'center'
      context.textBaseline = 'middle'
      const position = placeAnnotationLabel(
        context,
        chart,
        occupiedAnnotations,
        'MCB',
        x,
        y,
        bullishChannel ? 1 : -1,
        -5,
        5,
        true,
      )
      if (position !== null) {
        context.fillText('MCB', position.x, position.y)
      }
      context.restore()
    })
}

function drawVolumes(context, chart, bars, colors) {
  const bodyWidth = Math.max(1, Math.min(7, chart.barWidth * 0.62))
  const closedGroups = [
    { rise: true, forming: false, alpha: 0.38 },
    { rise: false, forming: false, alpha: 0.38 },
  ]
  const groups = bars.some((bar) => bar.closed === false)
    ? [
        ...closedGroups,
        { rise: true, forming: true, alpha: 0.18 },
        { rise: false, forming: true, alpha: 0.18 },
      ]
    : closedGroups
  context.save()
  groups.forEach((group) => {
    context.beginPath()
    let hasBars = false
    bars.forEach((bar, index) => {
      if (bar.close >= bar.open !== group.rise || (bar.closed === false) !== group.forming) {
        return
      }
      const height = (bar.volume / chart.volumeMax) * (chart.volumeBottom - chart.volumeTop)
      context.rect(barX(index, chart) - bodyWidth / 2, chart.volumeBottom - height, bodyWidth, height)
      hasBars = true
    })
    if (hasBars) {
      context.globalAlpha = group.alpha
      context.fillStyle = group.rise ? colors.rise : colors.fall
      context.fill()
    }
  })
  context.restore()
  context.fillStyle = colors.axis
  context.font = '10px Roboto, sans-serif'
  context.textAlign = 'left'
  context.fillText('VOL', chart.plotLeft, chart.volumeTop - 6)
}

function drawCandles(context, chart, bars, colors, riseCandleStyle) {
  const bodyWidth = Math.max(1.2, Math.min(7, chart.barWidth * 0.66))
  const closedGroups = [
    { rise: true, forming: false },
    { rise: false, forming: false },
  ]
  const groups = bars.some((bar) => bar.closed === false)
    ? [{ rise: true, forming: true }, { rise: false, forming: true }, ...closedGroups]
    : closedGroups
  let currentForming = null
  context.save()
  groups.forEach((group) => {
    const color = group.rise ? colors.rise : colors.fall
    if (currentForming !== group.forming) {
      currentForming = group.forming
      context.setLineDash(group.forming ? [2, 2] : [])
    }
    context.globalAlpha = group.forming ? 0.58 : 1
    context.strokeStyle = color
    context.lineWidth = 1
    context.beginPath()
    let hasBars = false
    bars.forEach((bar, index) => {
      if (bar.close >= bar.open !== group.rise || (bar.closed === false) !== group.forming) {
        return
      }
      const x = barX(index, chart)
      context.moveTo(x, priceY(bar.high, chart))
      context.lineTo(x, priceY(bar.low, chart))
      hasBars = true
    })
    if (hasBars) {
      context.stroke()
    }

    context.beginPath()
    bars.forEach((bar, index) => {
      if (bar.close >= bar.open !== group.rise || (bar.closed === false) !== group.forming) {
        return
      }
      const x = barX(index, chart)
      const openY = priceY(bar.open, chart)
      const closeY = priceY(bar.close, chart)
      const bodyTop = Math.min(openY, closeY)
      const bodyHeight = Math.max(1, Math.abs(closeY - openY))
      context.rect(x - bodyWidth / 2, bodyTop, bodyWidth, bodyHeight)
    })
    if (hasBars) {
      if (group.forming || (group.rise && riseCandleStyle === 'hollow')) {
        context.fillStyle = colors.background
        context.fill()
        context.strokeStyle = color
        context.stroke()
      } else {
        context.fillStyle = color
        context.fill()
      }
    }
  })
  context.restore()
}

function drawStructureMarkers(
  context,
  chart,
  analysis,
  bars,
  colors,
  barOffset,
  occupiedAnnotations,
  showBos,
  showChoch,
) {
  analysis.structures.forEach((event) => {
    if ((event.label === 'BOS' && !showBos) || (event.label === 'CHoCH' && !showChoch)) {
      return
    }
    const localIndex = event.index - barOffset
    if (localIndex < 0 || localIndex >= bars.length) {
      return
    }
    const nearbyBars = bars.slice(Math.max(0, localIndex - 2), Math.min(bars.length, localIndex + 3))
    const bullish = event.direction === 'bullish'
    const anchorPrice = bullish
      ? Math.max(...nearbyBars.map((bar) => bar.high))
      : Math.min(...nearbyBars.map((bar) => bar.low))
    const x = barX(localIndex, chart)
    const rawY = priceY(anchorPrice, chart) + (bullish ? -12 : 14)
    const color = bullish ? colors.rise : colors.fall
    const candleEdgeY = priceY(bullish ? bars[localIndex].high : bars[localIndex].low, chart)
    const brokenY = priceY(event.brokenPrice, chart)
    const brokenLocalIndex = event.brokenIndex - barOffset
    const brokenX =
      brokenLocalIndex < 0
        ? chart.plotLeft
        : brokenLocalIndex >= bars.length
          ? chart.plotRight
          : barX(brokenLocalIndex, chart)
    const isChoch = event.label === 'CHoCH'
    context.save()
    context.strokeStyle = color
    context.fillStyle = color
    context.font = 'bold 8px Roboto, sans-serif'
    context.textAlign = 'center'
    context.textBaseline = 'middle'
    if (brokenY >= chart.plotTop && brokenY <= chart.plotBottom) {
      context.globalAlpha = isChoch ? 0.72 : 0.5
      context.lineWidth = isChoch ? 1.5 : 1
      context.setLineDash(isChoch ? [4, 3] : [])
      context.beginPath()
      context.moveTo(Math.max(chart.plotLeft, brokenX), brokenY)
      context.lineTo(x, brokenY)
      context.stroke()
      context.setLineDash([])
      context.globalAlpha = 1
    }
    const position = placeAnnotationLabel(
      context,
      chart,
      occupiedAnnotations,
      event.label,
      x,
      rawY,
      bullish ? -1 : 1,
      -6,
      6,
      true,
    )
    if (position === null) {
      context.restore()
      return
    }
    const guideStartY = bullish ? position.y + 5 : position.y - 5
    const guideEndY = candleEdgeY + (bullish ? -3 : 3)
    const hasGuideSpace = bullish ? guideStartY < guideEndY : guideStartY > guideEndY
    if (hasGuideSpace) {
      context.globalAlpha = 0.55
      context.lineWidth = 1
      context.setLineDash([2, 2])
      context.beginPath()
      context.moveTo(position.x, guideStartY)
      context.lineTo(x, guideEndY)
      context.stroke()
      context.setLineDash([])
      context.globalAlpha = 1
    }
    context.beginPath()
    context.arc(x, candleEdgeY, 1.5, 0, Math.PI * 2)
    context.fill()
    if (isChoch) {
      const labelWidth = context.measureText(event.label).width + 8
      context.fillStyle = colors.background
      context.globalAlpha = 0.9
      roundedRect(context, position.x - labelWidth / 2, position.y - 6, labelWidth, 12, 3)
      context.fill()
      context.globalAlpha = 1
      context.strokeStyle = color
      context.lineWidth = 1
      context.stroke()
      context.fillStyle = color
    }
    context.fillText(event.label, position.x, position.y)
    context.restore()
  })
}

function breakoutTransitionAbbreviation(phase) {
  const labels = {
    'intrabar-break': 'IB',
    'close-break': 'CB',
    'awaiting-follow-through': '…',
    'confirmed-breakout': 'FT',
    'breakout-retest': 'RT',
    continuation: 'CT',
    'failed-breakout': 'FB×',
  }
  return labels[phase]
}

function drawBreakoutTransitions(
  context,
  chart,
  analysis,
  bars,
  colors,
  barOffset,
  occupiedAnnotations,
  showBos,
  showChoch,
) {
  const visibleTransitions = analysis.breakouts
    .flatMap((breakout) => {
      if (
        (breakout.structureLabel === 'BOS' && !showBos) ||
        (breakout.structureLabel === 'CHoCH' && !showChoch) ||
        (breakout.structureLabel === null && !showBos && !showChoch)
      ) {
        return []
      }
      return breakout.transitions
        .filter((transition) => {
          if (transition.phase === 'close-break' || transition.phase === 'awaiting-follow-through') {
            return false
          }
          if (transition.phase === 'intrabar-break' && breakout.closeBreakIndex === transition.index) {
            return false
          }
          return transition.index >= barOffset && transition.index < barOffset + bars.length
        })
        .map((transition) => ({ breakout, transition }))
    })
    .sort((left, right) => left.transition.index - right.transition.index)
    .slice(-10)

  visibleTransitions.forEach(({ breakout, transition }) => {
    const localIndex = transition.index - barOffset
    const bar = bars[localIndex]
    if (!bar) {
      return
    }
    const bullish = breakout.direction === 'bullish'
    const side = bullish ? -1 : 1
    const x = barX(localIndex, chart)
    const rawY = priceY(bullish ? bar.high : bar.low, chart) + side * 12
    const label = breakoutTransitionAbbreviation(transition.phase)
    const failed = transition.phase === 'failed-breakout'
    context.save()
    context.fillStyle = failed ? colors.neutral : bullish ? colors.rise : colors.fall
    context.strokeStyle = context.fillStyle
    context.globalAlpha = failed ? 0.75 : 0.86
    context.font = 'bold 7px Roboto, sans-serif'
    context.textAlign = 'center'
    context.textBaseline = 'middle'
    const position = placeAnnotationLabel(
      context,
      chart,
      occupiedAnnotations,
      label,
      x,
      rawY,
      side,
      -5,
      5,
      true,
      3,
    )
    if (position !== null) {
      context.fillText(label, position.x, position.y)
    }
    context.restore()
  })
}

function drawSwingLabels(context, chart, analysis, bars, colors, barOffset, occupiedAnnotations) {
  analysis.swings.slice(-12).forEach((swing) => {
    if (swing.label === 'H' || swing.label === 'L') {
      return
    }
    const localIndex = swing.index - barOffset
    if (localIndex < 0 || localIndex >= bars.length) {
      return
    }
    const x = barX(localIndex, chart)
    const rawY = priceY(swing.price, chart) + (swing.type === 'high' ? -10 : 10)
    const bullishStructure = swing.label === 'HH' || swing.label === 'HL'
    const bearishStructure = swing.label === 'LH' || swing.label === 'LL'
    context.save()
    context.fillStyle = bullishStructure ? colors.rise : bearishStructure ? colors.fall : colors.neutral
    context.globalAlpha =
      swing.level === 'major' ? 0.95 : swing.label === 'EH' || swing.label === 'EL' ? 0.58 : 0.68
    context.font = swing.level === 'major' ? 'bold 8px Roboto, sans-serif' : 'bold 7px Roboto, sans-serif'
    context.textAlign = 'center'
    context.textBaseline = 'middle'
    const displayLabel = swing.level === 'major' ? `M·${swing.label}` : swing.label
    const position = placeAnnotationLabel(
      context,
      chart,
      occupiedAnnotations,
      displayLabel,
      x,
      rawY,
      swing.type === 'high' ? -1 : 1,
      -5,
      5,
      false,
      1,
    )
    if (position === null) {
      context.restore()
      return
    }
    context.fillText(displayLabel, position.x, position.y)
    context.restore()
  })
}

function drawBarCountMarkers(context, chart, analysis, bars, colors, barOffset, occupiedAnnotations) {
  analysis.barCounts.slice(-10).forEach((event) => {
    const localIndex = event.index - barOffset
    const bar = bars[localIndex]
    if (!bar) {
      return
    }
    const bullish = event.direction === 'bullish'
    const x = barX(localIndex, chart)
    const rawY = priceY(bullish ? bar.low : bar.high, chart) + (bullish ? 12 : -12)
    context.save()
    context.fillStyle = bullish ? colors.rise : colors.fall
    context.globalAlpha = event.followThrough === 'failed' ? 0.48 : 0.9
    context.font = 'bold 8px Roboto, sans-serif'
    context.textAlign = 'center'
    context.textBaseline = 'middle'
    const displayLabel = `${event.label}${event.followThrough === 'strong' ? '✓' : event.followThrough === 'failed' ? '×' : ''}`
    const position = placeAnnotationLabel(
      context,
      chart,
      occupiedAnnotations,
      displayLabel,
      x,
      rawY,
      bullish ? 1 : -1,
      -5,
      5,
      false,
      2,
    )
    if (position !== null) {
      context.fillText(displayLabel, position.x, position.y)
    }
    context.restore()
  })
}

function drawSignalMarkers(
  context,
  chart,
  analysis,
  bars,
  colors,
  barOffset,
  occupiedAnnotations,
  visibleSignalTypes,
) {
  groupSignals(analysis.signals.filter((signal) => visibleSignalTypes.has(signal.type)))
    .slice(-6)
    .forEach((group) => {
      const localIndex = group.index - barOffset
      const bar = bars[localIndex]
      if (!bar) {
        return
      }
      const directions = new Set(group.signals.map((signal) => signal.direction))
      const direction = directions.size === 1 ? group.signals[0].direction : 'neutral'
      const statuses = new Set(group.signals.map((signal) => signal.status))
      const status = statuses.has('pending') ? 'pending' : statuses.has('confirmed') ? 'confirmed' : 'failed'
      const x = barX(localIndex, chart)
      const bullish = direction === 'bullish'
      const neutral = direction === 'neutral'
      const y = bullish
        ? Math.min(priceY(bar.low, chart) + 10, chart.plotBottom - 14)
        : Math.max(priceY(bar.high, chart) - 10, chart.plotTop + 14)
      context.save()
      const color = neutral ? colors.neutral : bullish ? colors.rise : colors.fall
      context.fillStyle = color
      context.strokeStyle = color
      context.globalAlpha = status === 'failed' ? 0.48 : 0.92
      context.font = 'bold 8px Roboto, sans-serif'
      context.textAlign = 'center'
      context.textBaseline = 'middle'
      const abbreviation = signalGroupLabel(group.signals)
      const preferredLabelY = bullish ? y + 9 : y - 9
      const position = placeAnnotationLabel(
        context,
        chart,
        occupiedAnnotations,
        abbreviation,
        x,
        preferredLabelY,
        bullish ? 1 : -1,
        bullish ? -16 : -6,
        bullish ? 6 : 16,
      )
      if (position === null) {
        context.restore()
        return
      }
      const markerY = bullish ? position.y - 9 : position.y + 9
      context.beginPath()
      if (status === 'failed') {
        context.moveTo(position.x - 3.5, markerY - 3.5)
        context.lineTo(position.x + 3.5, markerY + 3.5)
        context.moveTo(position.x + 3.5, markerY - 3.5)
        context.lineTo(position.x - 3.5, markerY + 3.5)
      } else if (neutral) {
        context.moveTo(position.x, markerY - 4)
        context.lineTo(position.x + 4, markerY)
        context.lineTo(position.x, markerY + 4)
        context.lineTo(position.x - 4, markerY)
      } else if (bullish) {
        context.moveTo(position.x, markerY - 5)
        context.lineTo(position.x - 3.5, markerY + 1)
        context.lineTo(position.x + 3.5, markerY + 1)
      } else {
        context.moveTo(position.x, markerY + 5)
        context.lineTo(position.x - 3.5, markerY - 1)
        context.lineTo(position.x + 3.5, markerY - 1)
      }
      if (status !== 'failed') {
        context.closePath()
      }
      if (status === 'confirmed') {
        context.fill()
      } else {
        context.lineWidth = 1.25
        context.stroke()
      }
      context.fillText(abbreviation, position.x, position.y)
      context.restore()
    })
}

function drawCurrentPrice(context, chart, bars, colors) {
  const last = bars[bars.length - 1]
  const y = priceY(last.close, chart)
  const forming = last.closed === false
  const label = `${forming ? '实 ' : ''}${formatPrice(last.close)}`
  const labelWidth = forming ? 50 : 48
  context.save()
  context.strokeStyle = colors.current
  context.globalAlpha = forming ? 0.42 : 0.6
  context.setLineDash(forming ? [1, 3] : [2, 3])
  context.beginPath()
  context.moveTo(chart.plotLeft, y)
  context.lineTo(chart.plotRight, y)
  context.stroke()
  context.setLineDash([])
  context.globalAlpha = 1
  context.fillStyle = last.close >= last.open ? colors.rise : colors.fall
  roundedRect(context, chart.plotRight + 2, y - 9, labelWidth, 18, 4)
  context.fill()
  if (forming) {
    context.strokeStyle = colors.background
    context.globalAlpha = 0.75
    context.setLineDash([2, 2])
    context.stroke()
    context.setLineDash([])
    context.globalAlpha = 1
  }
  context.fillStyle = '#ffffff'
  context.font = 'bold 10px Roboto, sans-serif'
  context.textAlign = 'center'
  context.fillText(label, chart.plotRight + 2 + labelWidth / 2, y)
  context.restore()
}

function markerSummary(analysis, analysisIndex) {
  const parts = []
  const classification = analysis.barClassifications[analysisIndex]
  if (classification && classification.kind !== 'other') {
    parts.push(`${classification.label} ${Math.round(classification.score * 100)}`)
  }
  const swings = analysis.swings.filter((swing) => swing.index === analysisIndex).map((swing) => swing.label)
  if (swings.length > 0) {
    parts.push(swings.join('/'))
  }
  const structures = analysis.structures
    .filter((event) => event.index === analysisIndex)
    .map((event) => event.label)
  if (structures.length > 0) {
    parts.push(structures.join('/'))
  }
  const legEnds = analysis.legs
    .filter((leg) => leg.endIndex === analysisIndex)
    .map((leg) => `${leg.level === 'major' ? '主要' : '次要'}${leg.direction === 'bull' ? '上行' : '下行'}腿`)
  if (legEnds.length > 0) {
    parts.push(legEnds.join('/'))
  }
  const breakoutPhases = analysis.breakouts.flatMap((breakout) =>
    breakout.transitions
      .filter(
        (transition) =>
          transition.index === analysisIndex &&
          transition.phase !== 'close-break' &&
          transition.phase !== 'awaiting-follow-through',
      )
      .map((transition) => breakoutTransitionAbbreviation(transition.phase)),
  )
  if (breakoutPhases.length > 0) {
    parts.push(`突破 ${Array.from(new Set(breakoutPhases)).join('/')}`)
  }
  const channelEvents = analysis.channelEvents
    .filter((event) => event.index === analysisIndex)
    .map((event) => event.label)
  if (channelEvents.length > 0) {
    parts.push(channelEvents.join('/'))
  }
  const microChannelBreaks = analysis.microChannelBreaks
    .filter((event) => event.index === analysisIndex)
    .map((event) => (event.channelDirection === 'bullish' ? '牛微通道突破' : '熊微通道突破'))
  if (microChannelBreaks.length > 0) {
    parts.push(microChannelBreaks.join('/'))
  }
  const signals = analysis.signals.filter((signal) => signal.index === analysisIndex)
  if (signals.length > 0) {
    const contexts = Array.from(new Set(signals.map((signal) => signal.contextLabel)))
    parts.push(`${signalGroupLabel(signals)} ${contexts.join('/')}`)
  }
  const relatedObjects = (analysis.objects || [])
    .filter(
      (object) =>
        object.knownAtIndex === analysisIndex ||
        object.anchors.some((anchor) => anchor.index === analysisIndex && anchor.role !== '投影终点'),
    )
    .sort((left, right) => right.score - left.score)
    .slice(0, 2)
    .map(
      (object) =>
        `${object.title}${object.status === 'failed' ? '×' : object.status === 'candidate' ? '?' : ''}`,
    )
  if (relatedObjects.length > 0) {
    parts.push(Array.from(new Set(relatedObjects)).join('/'))
  }
  return parts.join(' · ')
}

function takeTextPrefix(context, text, maximumWidth) {
  const characters = Array.from(text)
  if (characters.length === 0 || context.measureText(text).width <= maximumWidth) {
    return [text, '']
  }
  let low = 0
  let high = characters.length
  while (low < high) {
    const middle = Math.ceil((low + high) / 2)
    if (context.measureText(characters.slice(0, middle).join('')).width <= maximumWidth) {
      low = middle
    } else {
      high = middle - 1
    }
  }
  const length = Math.max(1, low)
  return [characters.slice(0, length).join(''), characters.slice(length).join('')]
}

function truncateCanvasText(context, text, maximumWidth, forceEllipsis = false) {
  if (!forceEllipsis && context.measureText(text).width <= maximumWidth) return text
  const ellipsis = '…'
  const availableWidth = Math.max(0, maximumWidth - context.measureText(ellipsis).width)
  const [prefix] = takeTextPrefix(context, text, availableWidth)
  return `${prefix}${ellipsis}`
}

function wrapCanvasText(context, text, maximumWidth, maximumLines) {
  if (!text || maximumLines <= 0 || maximumWidth <= 0) return []
  const lines = []
  let currentLine = ''
  for (const part of text.split(' · ')) {
    const candidate = currentLine ? `${currentLine} · ${part}` : part
    if (context.measureText(candidate).width <= maximumWidth) {
      currentLine = candidate
      continue
    }
    if (currentLine) {
      lines.push(currentLine)
      currentLine = ''
    }
    let remainder = part
    while (remainder) {
      const [prefix, nextRemainder] = takeTextPrefix(context, remainder, maximumWidth)
      if (!nextRemainder) {
        currentLine = prefix
        break
      }
      lines.push(prefix)
      remainder = nextRemainder
    }
  }
  if (currentLine) lines.push(currentLine)
  if (lines.length <= maximumLines) return lines
  const visibleLines = lines.slice(0, maximumLines)
  visibleLines[maximumLines - 1] = truncateCanvasText(
    context,
    visibleLines[maximumLines - 1],
    maximumWidth,
    true,
  )
  return visibleLines
}

function drawCrosshair(context, chart, bars, selectedIndex, colors, analysis, barOffset) {
  const index = Math.max(0, Math.min(bars.length - 1, selectedIndex))
  const bar = bars[index]
  const x = barX(index, chart)
  const y = priceY(bar.close, chart)
  const summary = markerSummary(analysis, index + barOffset)
  context.save()
  context.strokeStyle = colors.crosshair
  context.setLineDash([3, 3])
  context.beginPath()
  context.moveTo(x, chart.plotTop)
  context.lineTo(x, chart.volumeBottom)
  context.moveTo(chart.plotLeft, y)
  context.lineTo(chart.plotRight, y)
  context.stroke()
  context.setLineDash([])
  const boxWidth = Math.min(210, chart.plotRight - chart.plotLeft - 8)
  const maximumTextWidth = boxWidth - 14
  context.font = 'bold 10px Roboto, sans-serif'
  const summaryLines = wrapCanvasText(context, summary, maximumTextWidth, 3)
  const boxHeight = summaryLines.length > 0 ? 43 + summaryLines.length * 15 : 42
  const boxX = x > chart.width / 2 ? chart.plotLeft + 4 : chart.plotRight - boxWidth - 4
  context.fillStyle = colors.tooltip
  roundedRect(context, boxX, chart.plotTop + 4, boxWidth, boxHeight, 6)
  context.fill()
  context.fillStyle = colors.text
  context.font = '11px Roboto, sans-serif'
  context.textAlign = 'left'
  context.fillText(
    truncateCanvasText(
      context,
      `${shortDate(bar.date)}${bar.closed === false ? '  形成中' : ''}  O ${formatPrice(bar.open)}  C ${formatPrice(bar.close)}`,
      maximumTextWidth,
    ),
    boxX + 7,
    chart.plotTop + 17,
  )
  context.fillText(
    truncateCanvasText(
      context,
      `H ${formatPrice(bar.high)}  L ${formatPrice(bar.low)}  V ${Math.round(bar.volume)}`,
      maximumTextWidth,
    ),
    boxX + 7,
    chart.plotTop + 34,
  )
  if (summaryLines.length > 0) {
    context.font = 'bold 10px Roboto, sans-serif'
    summaryLines.forEach((line, lineIndex) => {
      context.fillText(line, boxX + 7, chart.plotTop + 51 + lineIndex * 15)
    })
  }
  context.restore()
}

export function drawPriceActionCrosshair(
  context,
  bars,
  analysis,
  width,
  height,
  selectedIndex,
  theme = 'light',
  barOffset = 0,
  layers = DEFAULT_DRAWABLE_LAYERS,
) {
  context.clearRect(0, 0, width, height)
  if (selectedIndex === null || bars.length === 0) {
    return
  }
  const chart = geometry(bars, width, height, layers.volume)
  const colors = { ...COLOR_THEMES[theme], ...PRICE_COLOR_SCHEMES['red-green'] }
  drawCrosshair(context, chart, bars, selectedIndex, colors, analysis, barOffset)
}

export function drawPriceActionChartPreview(
  context,
  bars,
  width,
  height,
  theme = 'light',
  colorScheme = 'red-green',
  riseCandleStyle = 'hollow',
  showVolume = true,
  visibleBarCount = bars.length,
) {
  const chart = geometry(bars, width, height, showVolume, visibleBarCount)
  const colors = { ...COLOR_THEMES[theme], ...PRICE_COLOR_SCHEMES[colorScheme] }
  context.clearRect(0, 0, width, height)
  context.fillStyle = colors.background
  context.fillRect(0, 0, width, height)
  drawGrid(context, chart, bars, colors)
  context.save()
  context.beginPath()
  context.rect(
    chart.plotLeft,
    chart.plotTop,
    chart.plotRight - chart.plotLeft,
    chart.volumeBottom - chart.plotTop,
  )
  context.clip()
  if (showVolume) {
    drawVolumes(context, chart, bars, colors)
  }
  drawCandles(context, chart, bars, colors, riseCandleStyle)
  context.restore()
  drawCurrentPrice(context, chart, bars, colors)
  return {
    width,
    height,
    plotLeft: chart.plotLeft,
    plotRight: chart.plotRight,
    plotTop: chart.plotTop,
    plotBottom: chart.plotBottom,
    barWidth: chart.barWidth,
  }
}

function drawKlineTransitionFrame(
  context,
  bars,
  width,
  height,
  theme,
  colorScheme,
  riseCandleStyle,
  showVolume,
) {
  const chart = geometry(bars, width, height, showVolume)
  const colors = { ...COLOR_THEMES[theme], ...PRICE_COLOR_SCHEMES[colorScheme] }
  context.clearRect(0, 0, width, height)
  context.fillStyle = colors.background
  context.fillRect(0, 0, width, height)
  drawGrid(context, chart, bars, colors)
  if (showVolume) {
    drawVolumes(context, chart, bars, colors)
  }
  drawCandles(context, chart, bars, colors, riseCandleStyle)
  drawCurrentPrice(context, chart, bars, colors)
  return {
    width,
    height,
    plotLeft: chart.plotLeft,
    plotRight: chart.plotRight,
    plotTop: chart.plotTop,
    plotBottom: chart.plotBottom,
    barWidth: chart.barWidth,
  }
}

export function animateKlineChartTransition(options) {
  const {
    canvas,
    context,
    fromBars,
    toBars,
    width,
    height,
    theme = 'light',
    colorScheme = 'red-green',
    riseCandleStyle = 'hollow',
    showVolume = true,
    duration = DEFAULT_TRANSITION_DURATION_MS,
    onFrame,
    onComplete,
  } = options
  const sourceBars = resampleBars(fromBars, toBars.length)
  const startedAt = Date.now()
  let frameId = null
  let cancelled = false

  const drawFrame = () => {
    if (cancelled) {
      return
    }
    const linearProgress = Math.min(1, (Date.now() - startedAt) / Math.max(1, duration))
    const progress = easeInOutCubic(linearProgress)
    const bars = toBars.map((bar, index) => interpolateBar(sourceBars[index] || bar, bar, progress))
    const viewport = drawKlineTransitionFrame(
      context,
      bars,
      width,
      height,
      theme,
      colorScheme,
      riseCandleStyle,
      showVolume,
    )
    if (onFrame) {
      onFrame(viewport)
    }
    if (linearProgress < 1) {
      frameId = canvas.requestAnimationFrame(drawFrame)
      return
    }
    frameId = null
    onComplete()
  }

  frameId = canvas.requestAnimationFrame(drawFrame)
  return {
    cancel() {
      cancelled = true
      if (frameId !== null) {
        canvas.cancelAnimationFrame(frameId)
        frameId = null
      }
    },
  }
}

export function drawPriceActionChart(
  context,
  bars,
  analysis,
  width,
  height,
  selectedIndex = null,
  theme = 'light',
  barOffset = 0,
  colorScheme = 'red-green',
  riseCandleStyle = 'hollow',
  showAnalysisOverlays = true,
  layers = DEFAULT_DRAWABLE_LAYERS,
) {
  const chart = geometry(bars, width, height, layers.volume)
  const colors = { ...COLOR_THEMES[theme], ...PRICE_COLOR_SCHEMES[colorScheme] }
  context.clearRect(0, 0, width, height)
  context.fillStyle = colors.background
  context.fillRect(0, 0, width, height)
  drawGrid(context, chart, bars, colors)
  if (showAnalysisOverlays && layers.higherTimeframe) {
    drawHigherTimeframeContext(context, chart, analysis, colors)
  }
  if (showAnalysisOverlays && layers.reversalPatterns) {
    drawReversalPatterns(context, chart, analysis, colors, barOffset, bars.length)
  }
  if (showAnalysisOverlays && layers.trendPatterns) {
    drawTrendPatterns(context, chart, analysis, colors, barOffset, bars.length)
  }
  if (showAnalysisOverlays && layers.measuredMoves) {
    drawMeasuredMoves(context, chart, analysis, colors, barOffset, bars.length)
    drawMagnetLevels(context, chart, analysis, colors)
  }
  if (showAnalysisOverlays && layers.tradingRanges) {
    drawTradingRanges(context, chart, analysis, colors, barOffset, bars.length)
  }
  if (showAnalysisOverlays && layers.gaps) {
    drawPriceGaps(context, chart, analysis, colors, barOffset, bars.length)
  }
  if (showAnalysisOverlays && (layers.support || layers.resistance)) {
    drawLevels(context, chart, analysis, colors, layers.support, layers.resistance)
  }
  if (layers.volume) {
    drawVolumes(context, chart, bars, colors)
  }
  drawCandles(context, chart, bars, colors, riseCandleStyle)
  if (showAnalysisOverlays) {
    const occupiedAnnotations = []
    if (layers.ema20) {
      drawEma20(context, chart, analysis, colors, barOffset, bars.length)
    }
    if (layers.legs) {
      drawPriceLegs(context, chart, analysis, colors, barOffset, bars.length)
    }
    if (layers.trendLines || layers.trendChannels) {
      drawTrendLines(context, chart, analysis, colors, barOffset, bars.length, layers.trendChannels)
      drawTrendChannelEvents(
        context,
        chart,
        analysis,
        bars,
        colors,
        barOffset,
        occupiedAnnotations,
        layers.trendChannels,
      )
    }
    if (layers.microChannels) {
      drawMicroChannels(context, chart, analysis, colors, barOffset, bars.length)
      drawMicroChannelBreaks(context, chart, analysis, bars, colors, barOffset, occupiedAnnotations)
    }
    if (layers.invalidation) {
      drawInvalidation(context, chart, analysis, colors)
    }
    if (layers.bos || layers.choch) {
      drawStructureMarkers(
        context,
        chart,
        analysis,
        bars,
        colors,
        barOffset,
        occupiedAnnotations,
        layers.bos,
        layers.choch,
      )
      drawBreakoutTransitions(
        context,
        chart,
        analysis,
        bars,
        colors,
        barOffset,
        occupiedAnnotations,
        layers.bos,
        layers.choch,
      )
    }
    const visibleSignalTypes = new Set()
    if (layers.pinBar) visibleSignalTypes.add('pin-bar')
    if (layers.insideBar) visibleSignalTypes.add('inside-bar')
    if (layers.engulfing) visibleSignalTypes.add('engulfing')
    if (layers.outsideBar) visibleSignalTypes.add('outside-bar')
    if (visibleSignalTypes.size > 0) {
      drawSignalMarkers(
        context,
        chart,
        analysis,
        bars,
        colors,
        barOffset,
        occupiedAnnotations,
        visibleSignalTypes,
      )
    }
    if (layers.barClassifications) {
      drawBarClassificationMarkers(context, chart, analysis, bars, colors, barOffset, occupiedAnnotations)
    }
    if (layers.barCounts) {
      drawBarCountMarkers(context, chart, analysis, bars, colors, barOffset, occupiedAnnotations)
    }
    if (layers.swings) {
      drawSwingLabels(context, chart, analysis, bars, colors, barOffset, occupiedAnnotations)
    }
  }
  drawCurrentPrice(context, chart, bars, colors)
  if (selectedIndex !== null) {
    drawCrosshair(context, chart, bars, selectedIndex, colors, analysis, barOffset)
  }
  return {
    width,
    height,
    plotLeft: chart.plotLeft,
    plotRight: chart.plotRight,
    plotTop: chart.plotTop,
    plotBottom: chart.plotBottom,
    barWidth: chart.barWidth,
  }
}

// Web overlay adapter: caller owns candles, axes, volume and pointer interaction.
export function drawPriceActionLayers(
  context,
  bars,
  analysis,
  chart,
  theme = 'light',
  barOffset = 0,
  layers = {},
  palette = {},
) {
  const colors = { ...COLOR_THEMES[theme], ...palette }
  const showAnalysisOverlays = true
  if (showAnalysisOverlays && layers.higherTimeframe) {
    drawHigherTimeframeContext(context, chart, analysis, colors)
  }
  if (showAnalysisOverlays && layers.reversalPatterns) {
    drawReversalPatterns(context, chart, analysis, colors, barOffset, bars.length)
  }
  if (showAnalysisOverlays && layers.trendPatterns) {
    drawTrendPatterns(context, chart, analysis, colors, barOffset, bars.length)
  }
  if (showAnalysisOverlays && layers.measuredMoves) {
    drawMeasuredMoves(context, chart, analysis, colors, barOffset, bars.length)
    drawMagnetLevels(context, chart, analysis, colors)
  }
  if (showAnalysisOverlays && layers.tradingRanges) {
    drawTradingRanges(context, chart, analysis, colors, barOffset, bars.length)
  }
  if (showAnalysisOverlays && layers.gaps) {
    drawPriceGaps(context, chart, analysis, colors, barOffset, bars.length)
  }
  if (showAnalysisOverlays && (layers.support || layers.resistance)) {
    drawLevels(context, chart, analysis, colors, layers.support, layers.resistance)
  }
  if (showAnalysisOverlays) {
    const occupiedAnnotations = []
    if (layers.ema20) {
      drawEma20(context, chart, analysis, colors, barOffset, bars.length)
    }
    if (layers.legs) {
      drawPriceLegs(context, chart, analysis, colors, barOffset, bars.length)
    }
    if (layers.trendLines || layers.trendChannels) {
      drawTrendLines(
        context,
        chart,
        layers.trendLines ? analysis : { ...analysis, trendLines: [] },
        colors,
        barOffset,
        bars.length,
        layers.trendChannels,
      )
      drawTrendChannelEvents(
        context,
        chart,
        analysis,
        bars,
        colors,
        barOffset,
        occupiedAnnotations,
        layers.trendChannels,
      )
    }
    if (layers.microChannels) {
      drawMicroChannels(context, chart, analysis, colors, barOffset, bars.length)
      drawMicroChannelBreaks(context, chart, analysis, bars, colors, barOffset, occupiedAnnotations)
    }
    if (layers.invalidation) {
      drawInvalidation(context, chart, analysis, colors)
    }
    if (layers.bos || layers.choch) {
      drawStructureMarkers(
        context,
        chart,
        analysis,
        bars,
        colors,
        barOffset,
        occupiedAnnotations,
        layers.bos,
        layers.choch,
      )
      drawBreakoutTransitions(
        context,
        chart,
        analysis,
        bars,
        colors,
        barOffset,
        occupiedAnnotations,
        layers.bos,
        layers.choch,
      )
    }
    const visibleSignalTypes = new Set()
    if (layers.pinBar) visibleSignalTypes.add('pin-bar')
    if (layers.insideBar) visibleSignalTypes.add('inside-bar')
    if (layers.engulfing) visibleSignalTypes.add('engulfing')
    if (layers.outsideBar) visibleSignalTypes.add('outside-bar')
    if (visibleSignalTypes.size > 0) {
      drawSignalMarkers(
        context,
        chart,
        analysis,
        bars,
        colors,
        barOffset,
        occupiedAnnotations,
        visibleSignalTypes,
      )
    }
    if (layers.barClassifications) {
      drawBarClassificationMarkers(context, chart, analysis, bars, colors, barOffset, occupiedAnnotations)
    }
    if (layers.barCounts) {
      drawBarCountMarkers(context, chart, analysis, bars, colors, barOffset, occupiedAnnotations)
    }
    if (layers.swings) {
      drawSwingLabels(context, chart, analysis, bars, colors, barOffset, occupiedAnnotations)
    }
  }
}
