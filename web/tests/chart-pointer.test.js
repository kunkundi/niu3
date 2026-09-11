import assert from 'node:assert/strict'
import test from 'node:test'
import { createChartPointer, preventTouchMenu } from '../src/chart-pointer.js'

const touch = (x, y, pointerId = 1) => ({ pointerType: 'touch', pointerId, clientX: x, clientY: y })
function setup() {
  const calls = []
  return {
    calls,
    pointer: createChartPointer((event, activate, dragging) =>
      calls.push({ x: event.clientX, activate, dragging }),
    ),
  }
}

test('touch taps inspect once on release; small finger movement stays a tap', () => {
  const { calls, pointer } = setup()
  pointer.start(touch(100, 100))
  pointer.move(touch(103, 102))
  assert.equal(calls.length, 0)
  pointer.end(touch(103, 102))
  assert.deepEqual(calls, [{ x: 103, activate: true, dragging: false }])
})

test('horizontal drag inspects continuously and cannot also activate a buy marker', () => {
  const { calls, pointer } = setup()
  pointer.start(touch(100, 100))
  pointer.move(touch(125, 102))
  pointer.move(touch(170, 104))
  pointer.end(touch(170, 104))
  assert.deepEqual(
    calls.map((x) => x.x),
    [125, 170, 170],
  )
  assert.ok(calls.every((x) => x.activate && x.dragging))
  let prevented = false,
    stopped = false
  pointer.click({
    detail: 1,
    preventDefault() {
      prevented = true
    },
    stopPropagation() {
      stopped = true
    },
  })
  assert.ok(prevented && stopped)
})

test('vertical scrolling locks its direction and never changes chart context', () => {
  const { calls, pointer } = setup()
  pointer.start(touch(100, 100))
  pointer.move(touch(103, 125))
  pointer.move(touch(160, 130))
  pointer.end(touch(160, 130))
  assert.deepEqual(calls, [])
})

test('cancellation and multi-touch ignore remaining fingers and allow the next gesture', () => {
  const { calls, pointer } = setup()
  pointer.start(touch(100, 100))
  pointer.cancel(touch(100, 130))
  pointer.end(touch(100, 130))
  pointer.start(touch(100, 100))
  pointer.start(touch(200, 100, 2))
  pointer.move(touch(80, 100))
  pointer.end(touch(200, 100, 2))
  pointer.move(touch(70, 100))
  pointer.end(touch(70, 100))
  assert.deepEqual(calls, [])
  pointer.start(touch(50, 50))
  pointer.end(touch(50, 50))
  assert.equal(calls.length, 1)
})

test('mouse hover, primary clicks and keyboard clicks retain their existing behavior', () => {
  const { calls, pointer } = setup()
  pointer.move({ pointerType: 'mouse', clientX: 30 })
  pointer.start({ pointerType: 'mouse', button: 0, clientX: 40 })
  pointer.start({ pointerType: 'mouse', button: 2, clientX: 40 })
  assert.deepEqual(
    calls.map((x) => x.activate),
    [false, true],
  )
  pointer.cancel(touch(1, 1))
  pointer.click({
    detail: 0,
    preventDefault() {
      assert.fail('keyboard click blocked')
    },
  })
})

test('context menu suppression applies to touch controls, not text, fields or mouse clicks', () => {
  function menu(pointerType, control, editable) {
    let prevented = false
    preventTouchMenu({
      pointerType,
      target: {
        closest(selector) {
          return selector.startsWith('input') ? editable : control
        },
      },
      preventDefault() {
        prevented = true
      },
    })
    return prevented
  }
  assert.equal(menu('touch', true, false), true)
  assert.equal(menu('touch', false, false), false)
  assert.equal(menu('touch', true, true), false)
  assert.equal(menu('mouse', true, false), false)
})
