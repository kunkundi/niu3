// Allow the browser to scroll vertically or pinch to zoom; own only horizontal inspection.
export function createChartPointer(inspect) {
  const pointers = new Set()
  let gesture = null
  let dragged = false
  function start(event) {
    if (event.pointerType !== 'touch') {
      dragged = false
      if (event.button === 0) inspect(event, true)
      return
    }
    if (!pointers.size) dragged = false
    pointers.add(event.pointerId)
    if (pointers.size > 1) dragged = true
    gesture =
      pointers.size === 1 ? { id: event.pointerId, x: event.clientX, y: event.clientY, axis: null } : null
  }
  function move(event) {
    if (event.pointerType !== 'touch') {
      inspect(event, false)
      return
    }
    if (!gesture || gesture.id !== event.pointerId || pointers.size !== 1) return
    const dx = Math.abs(event.clientX - gesture.x),
      dy = Math.abs(event.clientY - gesture.y)
    if (!gesture.axis && Math.hypot(dx, dy) > 8) {
      gesture.axis = dx > dy * 1.15 ? 'horizontal' : 'vertical'
      dragged = true
    }
    if (gesture.axis === 'horizontal') inspect(event, true, true)
  }
  function end(event) {
    if (event.pointerType !== 'touch') return
    const current = gesture
    gesture = null
    pointers.delete(event.pointerId)
    if (!current || current.id !== event.pointerId || pointers.size) return
    const tapped = !current.axis && Math.hypot(event.clientX - current.x, event.clientY - current.y) <= 8
    if (!tapped) dragged = true
    if (current.axis === 'horizontal' || tapped) inspect(event, true, current.axis === 'horizontal')
  }
  function cancel(event) {
    pointers.delete(event.pointerId)
    gesture = null
    dragged = true
  }
  function click(event) {
    // A swipe that started over a buy marker must not also open that marker.
    if (dragged && event.detail !== 0) {
      event.preventDefault()
      event.stopPropagation()
      dragged = false
    }
  }
  return { start, move, end, cancel, click }
}

// Context menus on touch controls interrupt gestures. Ordinary text remains selectable.
export function preventTouchMenu(event) {
  if (event.target.closest('input, textarea, [contenteditable="true"]')) return
  if (
    !event.target.closest(
      'button, summary, [role="option"], label:has(> input:is([type="checkbox"], [type="radio"])), .terminal-nav, .brand, .text-link, .table-name, [data-touch-surface]',
    )
  )
    return
  if (
    event.pointerType === 'touch' ||
    event.sourceCapabilities?.firesTouchEvents ||
    (!event.pointerType && window.matchMedia('(any-pointer: coarse)').matches)
  )
    event.preventDefault()
}
