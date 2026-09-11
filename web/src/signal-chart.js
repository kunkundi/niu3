export const STRATEGY_LEVELS = [
  { id: 'entry', label: '入场触发', color: 'var(--accent)' },
  { id: 'exit', label: '卖出触发', color: 'var(--green)' },
  { id: 'entry_stop', label: '入场失效', color: 'var(--red)' },
  { id: 'support', label: '支撑', color: 'var(--green)' },
  { id: 'resistance', label: '压力', color: 'var(--yellow)' },
  { id: 'structural_stop', label: '摆动失效', color: 'var(--muted)' },
  { id: 'target', label: '止盈目标', color: 'var(--ma-60)' },
]

export function strategyLevels(pa, matched = true) {
  if (!matched || !pa?.ready) return []
  return STRATEGY_LEVELS.map((level) => ({ ...level, value: Number(pa[level.id]) })).filter(
    (level) => Number.isFinite(level.value) && level.value > 0,
  )
}

// Current conditions never appear in a viewport ending before the signal's basis day.
export function visibleStrategyLevels(levels, lastDay, asOf) {
  return asOf && lastDay === asOf ? levels.filter((l) => Number.isFinite(l.value) && l.value > 0) : []
}

export function priceLineLayout(levels, low, high, top = 14, bottom = 240, minimumGap = 21) {
  if (!(high > low)) return []
  const sorted = levels
    .map((level) => ({ ...level, y: bottom - ((level.value - low) / (high - low)) * (bottom - top) }))
    .sort((a, b) => a.y - b.y)
  const gap = Math.min(minimumGap, (bottom - top) / Math.max(sorted.length, 1))
  sorted.forEach((level, i) => {
    level.labelY = Math.max(top, level.y, i ? sorted[i - 1].labelY + gap : top)
  })
  for (let i = sorted.length - 1; i >= 0; i--) {
    sorted[i].labelY = Math.min(sorted[i].labelY, i < sorted.length - 1 ? sorted[i + 1].labelY - gap : bottom)
  }
  return sorted
}
