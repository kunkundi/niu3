import assert from 'node:assert/strict'
import test from 'node:test'
import { buildDirectionContext, summarizeDirection } from '../src/price-action/direction-context.js'
import { createPriceActionEngine } from '../src/price-action/engine.js'

const engine = createPriceActionEngine(0.01)
function scenario(sign = -1) {
  const bars = Array.from({ length: 40 }, (_, index) => {
    const close = 100 + sign * index * 0.2
    return {
      date: `bar-${index}`,
      open: close - sign * 0.1,
      high: close + 0.15,
      low: close - 0.15,
      close,
      closed: true,
    }
  })
  return {
    bars,
    atr: 0.4,
    tick: 0.01,
    structureStartIndex: 0,
    trend: 'range',
    swings: [],
    breakouts: [
      {
        id: 'confirmed',
        direction: sign === 1 ? 'bullish' : 'bearish',
        boundaryIndex: 4,
        boundaryPrice: bars[4].close + sign * 0.15,
        closeBreakIndex: 5,
        followThroughIndex: 6,
        knownAtIndex: 6,
        phase: 'confirmed-breakout',
        followThrough: 'strong',
        failedIndex: null,
      },
    ],
  }
}
const context = (input) =>
  buildDirectionContext({ ...input, ema20: input.ema20 ?? engine.analyzeEma20(input.bars) })
const state = (input) => summarizeDirection(context(input)).state

function withSwingPairs(input, indices = [10, 15, 20, 25]) {
  const sign = input.bars.at(-1).close > input.bars[0].close ? 1 : -1
  input.swings = indices.map((index, offset) => {
    const type = (offset % 2 === 0) === (sign === 1) ? 'low' : 'high'
    return {
      type,
      index,
      knownAtIndex: index + 3,
      price: input.bars[index].close + (type === 'low' ? -0.15 : 0.15),
    }
  })
  return input
}

test('confirmed upward and downward breaks establish control independently of stale swing direction', () => {
  for (const sign of [-1, 1]) {
    const input = scenario(sign)
    input.trend = sign === 1 ? 'down' : 'up'
    assert.equal(state(input), sign === 1 ? 'long' : 'short')
    assert.equal(context(input).source, 'breakout')
    input.breakouts[0].followThrough = 'weak'
    assert.equal(state(input), sign === 1 ? 'long' : 'short')
  }
})

test('a pending, failed, future or expired breakout never establishes direction', () => {
  const input = scenario()
  for (const patch of [
    { followThroughIndex: null, phase: 'awaiting-follow-through', followThrough: 'none' },
    { phase: 'failed-breakout', failedIndex: 8 },
    { followThroughIndex: 40, knownAtIndex: 40 },
    { knownAtIndex: 40 },
    { boundaryIndex: -1 },
  ]) {
    assert.equal(state({ ...input, breakouts: [{ ...input.breakouts[0], ...patch }] }), 'unclear')
  }
  assert.equal(state({ ...input, structureStartIndex: 10 }), 'unclear')
})

test('re-entry after the historical monitor stops invalidates control permanently until a new confirmation', () => {
  for (const sign of [-1, 1]) {
    const input = scenario(sign)
    input.breakouts[0].phase = 'continuation'
    input.bars[24].close = input.breakouts[0].boundaryPrice - sign * 0.1
    assert.equal(state(input), 'unclear') // Last close is outside again, but the premise was lost.
  }
})

test('one tick of boundary tolerance is symmetric and is not invalidated by floating-point rounding', () => {
  for (const sign of [-1, 1]) {
    const input = scenario(sign)
    const boundary = input.breakouts[0].boundaryPrice
    input.bars[24].close = boundary - sign * input.tick
    assert.equal(state(input), sign === 1 ? 'long' : 'short')
    input.bars[24].close = boundary - sign * input.tick * 2
    assert.equal(state(input), 'unclear')
  }
})

test('a newer failed confirmation cannot resurrect an earlier successful breakout or the same stale swing trend', () => {
  const input = scenario()
  input.trend = 'down'
  input.swings = [{ type: 'high', index: 2, knownAtIndex: 5, price: 101 }]
  assert.equal(state(input), 'short')
  input.breakouts.push({
    ...input.breakouts[0],
    id: 'later-failed',
    closeBreakIndex: 25,
    followThroughIndex: 26,
    knownAtIndex: 28,
    phase: 'failed-breakout',
    failedIndex: 28,
  })
  assert.equal(state(input), 'unclear')
})

test('flat consolidation and a single EMA crossing do not create a new directional premise', () => {
  const input = scenario()
  input.ema20 = { points: input.bars.map((bar, index) => ({ index, value: 95 })) }
  assert.equal(state(input), 'unclear') // A flat EMA fails the slope check even with an intact break.
  const noPremise = scenario()
  noPremise.breakouts = []
  assert.equal(state(noPremise), 'unclear') // EMA alignment alone cannot invent structure.
  const onlyOneClose = scenario()
  onlyOneClose.ema20 = { points: [95, 96, 97, 98, 93].map((value, index) => ({ index: 35 + index, value })) }
  onlyOneClose.bars.slice(35, 39).forEach((bar, index) => {
    bar.close = 95.1 + index
  })
  assert.equal(state(onlyOneClose), 'unclear')
})

test('confirmed swing structure requires aligned prices and an unbroken latest swing boundary', () => {
  for (const sign of [-1, 1]) {
    const input = withSwingPairs(scenario(sign))
    input.breakouts = []
    assert.equal(state(input), sign === 1 ? 'long' : 'short')
    assert.equal(context(input).premiseKnownAtIndex, 28)
    input.swings[2].price = input.bars.at(-1).close + sign * 0.5
    assert.equal(state(input), 'unclear')
  }
})

test('post-failure confirmed swing pairs can restore either direction without reviving the old breakout', () => {
  for (const sign of [-1, 1]) {
    const input = scenario(sign)
    input.bars[24].close = input.breakouts[0].boundaryPrice - sign * 0.1
    withSwingPairs(input, [10, 15, 23, 32])
    assert.equal(state(input), 'unclear') // A pre-failure pivot confirmed later is still old evidence.
    withSwingPairs(input, [10, 15, 28, 32])
    assert.equal(state(input), sign === 1 ? 'long' : 'short')
    assert.equal(context(input).source, 'swings')
    assert.equal(context(input).breakoutId, null)
    assert.equal(context(input).premiseKnownAtIndex, 35)
    input.swings[3].knownAtIndex = 40
    assert.equal(state(input), 'unclear') // The new pair must actually be known today.
  }
})

test('current swing direction does not depend on older points or their major/minor reclassification', () => {
  for (const sign of [-1, 1]) {
    const input = withSwingPairs(scenario(sign))
    input.breakouts = []
    input.swings.unshift(
      { type: 'high', index: 2, knownAtIndex: 5, price: 101, level: 'major' },
      { type: 'low', index: 3, knownAtIndex: 6, price: 99, level: 'major' },
    )
    const before = context(input)
    input.structureStartIndex = 10
    input.swings.forEach((point) => {
      point.level = 'minor'
    })
    assert.deepEqual(context(input), before)
    assert.equal(state(input), sign === 1 ? 'long' : 'short')
  }
})

test('a broken swing premise, mixed high/low pairs, or insufficient confirmed points cannot establish direction', () => {
  for (const sign of [-1, 1]) {
    const input = withSwingPairs(scenario(sign))
    input.breakouts = []
    input.bars[30].close = input.swings[2].price - sign * 0.1
    assert.equal(state(input), 'unclear') // Price has returned, but this structure was invalidated.
    input.swings[3] = { ...input.swings[3], index: 34, knownAtIndex: 37, price: input.bars[34].close }
    assert.equal(state(input), 'unclear') // A later high/low cannot reset an already broken boundary.
    const mixed = withSwingPairs(scenario(sign))
    mixed.breakouts = []
    mixed.swings[3].price = mixed.swings[1].price - sign
    assert.equal(state(mixed), 'unclear')
    const missing = withSwingPairs(scenario(sign))
    missing.breakouts = []
    missing.swings.pop()
    assert.equal(state(missing), 'unclear')
  }
})

test('a confirmed opposite break can reverse control, while pending and conflicting confirmations cannot', () => {
  const input = scenario()
  for (let index = 30; index < 40; index++) input.bars[index].close = 94 + (index - 30) * 0.6
  const reversal = {
    id: 'reversal',
    direction: 'bullish',
    boundaryIndex: 28,
    boundaryPrice: 95,
    closeBreakIndex: 32,
    followThroughIndex: 33,
    knownAtIndex: 33,
    phase: 'confirmed-breakout',
    followThrough: 'strong',
    failedIndex: null,
  }
  input.breakouts.push({
    ...reversal,
    followThroughIndex: null,
    phase: 'awaiting-follow-through',
    followThrough: 'none',
  })
  assert.equal(state(input), 'unclear')
  input.breakouts[1] = reversal
  assert.equal(state(input), 'long')
  input.breakouts.push({ ...reversal, direction: 'bearish', id: 'conflict' })
  assert.equal(state(input), 'unclear')
})
