// A new query invalidates in-flight responses immediately, before the debounce expires.
export function createSuggestionLoader(lookup, publish, delay = 250) {
  let sequence = 0
  let timer
  let disposed = false
  function update(value) {
    if (disposed) return
    const query = value.trim()
    const request = ++sequence
    clearTimeout(timer)
    publish({ items: [], loading: !!query, error: '' })
    if (!query || disposed) return
    timer = setTimeout(async () => {
      try {
        const result = await lookup(query)
        if (!disposed && request === sequence) {
          publish({ items: result.items || [], loading: false, error: '' })
        }
      } catch (error) {
        if (!disposed && request === sequence) {
          publish({
            items: [],
            loading: false,
            error: error.name === 'AbortError' ? '提示查询超时，请稍后重试' : error.message,
          })
        }
      }
    }, delay)
  }
  function cancel() {
    sequence++
    clearTimeout(timer)
  }
  return {
    update,
    cancel,
    dispose() {
      disposed = true
      cancel()
    },
  }
}

export function fullEtfCode(value) {
  return /^(?:(?:sh)?5\d{5}|(?:sz)?1\d{5})$/i.test(value.trim())
}

export function etfInputCode(value) {
  const text = value.normalize('NFKC').trim()
  if (fullEtfCode(text)) return text.toLowerCase()
  // Accept a restored/pasted suggestion label, but never guess a code from a name.
  const label = text.match(/^.+\s+\(((?:sh|sz)?\d{6})\)$/i)
  return label && fullEtfCode(label[1]) ? label[1].toLowerCase() : ''
}
