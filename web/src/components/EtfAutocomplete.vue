<script setup>
import { displayCode } from '../display-code.js'
import { computed, onUnmounted, ref, watch } from 'vue'
import { api } from '../state'
import { createSuggestionLoader } from '../etf-suggestions'

const props = defineProps({ modelValue: { type: String, default: '' }, disabled: Boolean })
const emit = defineEmits(['update:modelValue', 'select'])
const query = ref(props.modelValue),
  selected = ref(null),
  input = ref(null)
const items = ref([]),
  loading = ref(false),
  error = ref(''),
  expanded = ref(false),
  active = ref(-1)
const selectionLabel = (item) => `${displayCode(item.name)} (${displayCode(item.symbol)})`
// Keep inline results visible on blur so dialog actions do not move before a click lands.
const showList = computed(() => expanded.value && !!query.value.trim() && !selected.value && !props.disabled)
const loader = createSuggestionLoader(
  (value) => api(`/etfs/suggestions?${new URLSearchParams({ q: value })}`),
  (result) => {
    items.value = result.items
    loading.value = result.loading
    error.value = result.error
    active.value = -1
  },
)
function edit(event) {
  if (props.disabled || event.isComposing) return
  const value = event.target.value
  // Browsers can emit a final input event after an IME selection or autofill.
  if (selected.value && value === selectionLabel(selected.value)) return
  if (query.value === value) return
  query.value = value
  selected.value = null
  emit('select', null)
  emit('update:modelValue', value)
  expanded.value = true
  loader.update(value)
}
watch(
  () => props.modelValue,
  (value) => {
    if (selected.value?.symbol === value) return
    if (value === query.value) return
    selected.value = null
    emit('select', null)
    query.value = value
    if (value && !props.disabled) loader.update(value)
    else loader.cancel()
  },
)
watch(
  () => props.disabled,
  (disabled) => {
    if (disabled) {
      loader.cancel()
      expanded.value = false
    }
  },
)
onUnmounted(loader.dispose)
function choose(item, event) {
  if (props.disabled || !item || item.watched) return
  selected.value = item
  query.value = selectionLabel(item)
  loader.cancel()
  expanded.value = false
  active.value = -1
  emit('update:modelValue', item.symbol)
  if (event?.pointerType === 'touch' || event?.sourceCapabilities?.firesTouchEvents) input.value?.blur()
  else input.value?.focus()
  emit('select', item)
}
function keydown(event) {
  if (event.key === 'Enter') event.preventDefault()
  if (props.disabled || event.isComposing || event.keyCode === 229) return
  if (event.key === 'Escape') {
    if (showList.value) event.preventDefault()
    expanded.value = false
    active.value = -1
  } else if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
    event.preventDefault()
    expanded.value = true
    if (!items.value.length) return
    active.value =
      event.key === 'ArrowDown'
        ? (active.value + 1) % items.value.length
        : (active.value <= 0 ? items.value.length : active.value) - 1
    input.value?.parentElement
      .querySelector(`#etf-option-${active.value}`)
      ?.scrollIntoView({ block: 'nearest' })
  } else if (event.key === 'Enter') {
    if (showList.value && active.value >= 0) choose(items.value[active.value])
  }
}
</script>

<template>
  <div class="etf-autocomplete">
    <input
      id="manual-etf-code"
      ref="input"
      :value="query"
      role="combobox"
      placeholder="输入 ETF 代码或名称，如 510300、沪深300"
      autocomplete="off"
      enterkeyhint="search"
      maxlength="60"
      :disabled="disabled"
      aria-autocomplete="list"
      aria-controls="etf-suggestion-list"
      :aria-expanded="showList"
      :aria-activedescendant="showList && active >= 0 ? `etf-option-${active}` : undefined"
      @focus="expanded = true"
      @input="edit"
      @compositionend="edit"
      @keydown="keydown"
    />
    <div v-if="showList" class="etf-suggestion-panel">
      <ul id="etf-suggestion-list" role="listbox" aria-label="ETF 匹配提示" :aria-busy="loading">
        <li
          v-for="(item, index) in items"
          :id="`etf-option-${index}`"
          :key="item.symbol"
          role="option"
          :aria-selected="index === active"
          :aria-disabled="!!item.watched"
          :class="{ active: index === active, watched: item.watched }"
          @pointerdown="$event.pointerType !== 'touch' && $event.preventDefault()"
          @mousedown.prevent
          @click="choose(item, $event)"
          @mousemove="active = index"
        >
          <span
            ><strong>{{ displayCode(item.name) }}</strong
            ><small
              >{{ item.symbol.startsWith('sh') ? '沪市' : '深市' }} · {{ displayCode(item.symbol) }}</small
            ></span
          >
          <span v-if="item.watched" class="added">已添加</span>
        </li>
      </ul>
      <p v-if="loading" role="status">正在查找 ETF…</p>
      <p v-else-if="error" role="status">{{ error }}</p>
      <p v-else-if="!items.length" role="status">未找到匹配 ETF，请检查代码或换个名称后重试</p>
      <p v-else class="hint">点击或按 ↑↓、Enter 选择，核对后再确认添加</p>
    </div>
  </div>
</template>

<style scoped>
.etf-autocomplete {
  position: relative;
  width: 100%;
  min-width: 0;
}
input {
  width: 100%;
  min-width: 0;
}
.etf-suggestion-panel {
  margin-top: 8px;
  width: 100%;
  z-index: 30;
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  background: var(--panel);
  color: var(--ink);
  box-shadow: 0 8px 24px #0002;
}
ul {
  max-height: 280px;
  overflow-y: auto;
  padding: 4px;
  margin: 0;
  list-style: none;
}
li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px;
  border-radius: var(--radius-control);
  cursor: pointer;
}
li:hover,
li.active {
  background: var(--accent-soft);
}
li.watched {
  cursor: not-allowed;
  color: var(--muted);
}
li > span:first-child {
  min-width: 0;
}
strong {
  display: block;
  font-size: 12px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
small {
  display: block;
  margin-top: 4px;
  color: var(--muted);
  font-size: 11px;
}
.added {
  white-space: nowrap;
  color: var(--muted);
  font-size: 10px;
}
p {
  padding: 10px 14px;
  margin: 0;
  font-size: 11px;
  line-height: 1.7;
  color: var(--muted);
}
.hint {
  border-top: 1px solid var(--line);
}
@media (max-width: 600px) {
  .etf-autocomplete {
    width: 100%;
  }
}
</style>
