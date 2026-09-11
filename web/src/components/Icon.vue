<script setup>
defineProps({ name: String, size: { type: Number, default: 20 } })
const paths = {
  overview: 'M3 3h7v7H3z M14 3h7v4h-7z M14 11h7v10h-7z M3 14h7v7H3z',
  market: 'M3 18V9 M9 18V4 M15 18v-7 M21 18V6 M2 22h20',
  signals: 'M3 16l5-5 4 3 8-10 M15 4h5v5 M3 21h18',
  account: 'M3 7h18v13H3z M3 7V4h14v3 M16 12h5v4h-5z',
  settings: 'M4 6h16 M4 12h16 M4 18h16 M8 3v6 M16 9v6 M10 15v6',
  arrow: 'M5 12h14 M13 6l6 6-6 6',
  chevron: 'M9 5l7 7-7 7',
  search: 'M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  filter: 'M3 4h18l-7 8v7l-4 2v-9z',
  pause: 'M8 5v14 M16 5v14',
  play: 'M7 4l13 8-13 8z',
  clock: 'M12 7v5l3 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  shield: 'M12 2l8 4v6c0 5-8 10-8 10S4 17 4 12V6z M8 12l3 3 5-6',
  close: 'M6 6l12 12 M6 18L18 6',
  logout: 'M9 4H4v16h5 M10 12h11 M17 8l4 4-4 4',
  check: 'M4 12l5 5L20 6',
  refresh: 'M20 8a9 9 0 1 0 1 7 M20 3v5h-5',
  warning: 'M12 3L2 21h20z M12 9v5 M12 17v1',
  sun: 'M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0 M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l1.4 1.4 M17.6 17.6L19 19 M5 19l1.4-1.4 M17.6 6.4L19 5',
  moon: 'M20.5 13A9 9 0 0 1 11 3.5 9 9 0 1 0 20.5 13z',
}
</script>
<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name] || paths.overview" />
  </svg>
</template>
