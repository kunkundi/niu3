<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import Icon from './Icon.vue'

const theme = ref(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light')
const target = computed(() => (theme.value === 'dark' ? '浅色' : '深色'))

function apply(value) {
  theme.value = value === 'dark' ? 'dark' : 'light'
  document.documentElement.dataset.theme = theme.value
  document.querySelector('meta[name="theme-color"]').content = theme.value === 'light' ? '#f3f4f5' : '#121315'
}
function toggle() {
  apply(theme.value === 'dark' ? 'light' : 'dark')
  try {
    localStorage.setItem('niuno3-theme', theme.value)
  } catch {
    // The current page can still switch themes when browser storage is unavailable.
  }
}
function sync(event) {
  if (event.key === 'niuno3-theme' || event.key === null) {
    try {
      if (event.storageArea === localStorage) apply(event.newValue)
    } catch {
      // Keep the current appearance if this browser cannot access local storage.
    }
  }
}
onMounted(() => window.addEventListener('storage', sync))
onUnmounted(() => window.removeEventListener('storage', sync))
</script>

<template>
  <button
    type="button"
    class="theme-toggle"
    :aria-label="`切换为${target}主题`"
    :title="`当前为${theme === 'dark' ? '深色' : '浅色'}主题，切换为${target}`"
    @click="toggle"
  >
    <Icon :name="theme === 'dark' ? 'sun' : 'moon'" :size="15" /><span>{{ target }}</span>
  </button>
</template>

<style scoped>
.theme-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  height: 30px;
  min-height: 30px;
  padding: 4px 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  background: var(--panel);
  color: var(--text-secondary);
  white-space: nowrap;
  font-size: 11px;
}
.theme-toggle:hover {
  border-color: var(--accent-border);
  color: var(--accent-text);
  background: var(--accent-soft);
}
@media (max-width: 1000px) {
  .theme-toggle {
    width: 32px;
    height: 32px;
    min-height: 32px;
    padding: 0;
  }
  .theme-toggle span {
    display: none;
  }
}
@media (max-width: 700px) {
  .theme-toggle {
    width: 44px;
    height: 44px;
    min-height: 44px;
  }
}
</style>
