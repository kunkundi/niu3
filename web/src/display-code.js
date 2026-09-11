// Presentation only: keep exchange-qualified symbols in API calls, routes and stored records.
export function displayCode(value) {
  return String(value ?? '').replace(/^(?:sh|sz|bj)(\d{6})$/i, '$1')
}

export function displayCodeText(value) {
  return String(value ?? '').replace(/(?<![a-z0-9])(?:sh|sz|bj)(\d{6})(?![a-z0-9])/gi, '$1')
}
