import assert from 'node:assert/strict'
import test from 'node:test'
import { strategyLevels, visibleStrategyLevels, priceLineLayout } from '../src/signal-chart.js'
import { loadIndicators, saveIndicators } from '../src/candles.js'

test('strategy lines use qfq signal values, never raw order units or malformed data', () => {
  const pa = {
    ready: true,
    entry: 1.31,
    exit: 1.28,
    entry_stop: 1.24,
    target: 1.45,
    support: null,
    resistance: Infinity,
    structural_stop: -1,
    raw: { entry: 1010000 },
  }
  assert.deepEqual(
    strategyLevels(pa).map((x) => x.value),
    [1.31, 1.28, 1.24, 1.45],
  )
  assert.deepEqual(strategyLevels(pa, false), [])
  assert.deepEqual(strategyLevels({ ...pa, ready: false }), [])
})

test('current strategy boundaries disappear when paging into history', () => {
  const levels = strategyLevels({ ready: true, entry: 1.1 })
  assert.deepEqual(visibleStrategyLevels(levels, '2026-09-03', '2026-09-04'), [])
  assert.deepEqual(visibleStrategyLevels(levels, '2026-09-04', '2026-09-04'), levels)
  assert.deepEqual(visibleStrategyLevels(levels, '2026-09-07', '2026-09-04'), [])
})

test('nearby level labels remain separated inside the plot without changing their price coordinates', () => {
  const levels = Array.from({ length: 6 }, (_, i) => ({ id: String(i), value: 1 + i / 10000 }))
  const labels = priceLineLayout(levels, 0.99, 1.01)
  for (const [i, label] of labels.entries()) {
    assert.ok(Number.isFinite(label.y) && Number.isFinite(label.labelY))
    assert.ok(label.labelY >= 14 && label.labelY <= 240)
    if (i) assert.ok(label.labelY - labels[i - 1].labelY >= 20)
    assert.ok(Math.abs(label.y - (240 - ((label.value - 0.99) / 0.02) * 226)) < 1e-8)
  }
})

test('selected ETF indicator preferences persist independently of market chart preferences', () => {
  const saved = new Map(),
    storage = { getItem: (key) => saved.get(key), setItem: (key, value) => saved.set(key, value) }
  const market = loadIndicators(storage),
    strategy = loadIndicators(storage, 'strategy')
  assert.equal(strategy.ema20, true)
  assert.equal(strategy.swings, true)
  assert.equal(strategy.volume, true)
  saveIndicators(storage, { ...strategy, ema20: false, macd: true }, 'strategy')
  assert.deepEqual(loadIndicators(storage), market)
  assert.equal(loadIndicators(storage, 'strategy').ema20, false)
  assert.equal(loadIndicators(storage, 'strategy').macd, true)
})
