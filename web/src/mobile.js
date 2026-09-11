import { onMounted, onUnmounted, ref } from 'vue'

// Keep layout-dependent interactions in sync when a device rotates or resizes.
export function useMobile() {
  const media = window.matchMedia('(max-width: 700px)')
  const mobile = ref(media.matches)
  const update = () => (mobile.value = media.matches)
  onMounted(() => media.addEventListener('change', update))
  onUnmounted(() => media.removeEventListener('change', update))
  return mobile
}
