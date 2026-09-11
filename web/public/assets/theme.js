// Apply the saved appearance before styles render; this script also works under the site's CSP.
;(() => {
  let theme = 'light'
  try {
    if (localStorage.getItem('niuno3-theme') === 'dark') theme = 'dark'
  } catch {
    // Storage can be unavailable in private or restricted browser contexts.
  }
  document.documentElement.dataset.theme = theme
  document.querySelector('meta[name="theme-color"]').content = theme === 'light' ? '#f3f4f5' : '#121315'
})()
