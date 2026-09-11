<script setup>
import { computed } from 'vue'
import { PA_GROUPS, PA_OPTIONS, PA_COMMON } from '../price-action/index.js'
const props = defineProps({ modelValue: Object })
const emit = defineEmits(['update:modelValue'])
const selectedCount = computed(() => PA_OPTIONS.filter(({ id }) => props.modelValue[id]).length)
function change(id, checked) {
  emit('update:modelValue', { ...props.modelValue, [id]: checked })
}
function select(mode) {
  emit('update:modelValue', {
    ...props.modelValue,
    ...Object.fromEntries(
      PA_OPTIONS.map(({ id }) => [id, mode === 'all' || (mode === 'common' && PA_COMMON.includes(id))]),
    ),
    ...(mode === 'common' ? { ema20: true } : {}),
  })
}
</script>
<template>
  <details class="pa-settings">
    <summary>
      价格行为图层 <span>已选 {{ selectedCount }} / {{ PA_OPTIONS.length }}</span>
    </summary>
    <div class="pa-actions">
      <button type="button" class="button secondary compact" @click="select('common')">常用结构</button>
      <button type="button" class="button secondary compact" @click="select('all')">全选结构</button>
      <button type="button" class="text-button" @click="select('none')">清空结构</button>
      <small>独立勾选 · 自动保存</small>
    </div>
    <fieldset v-for="group in PA_GROUPS" :key="group.label">
      <legend>{{ group.label }}</legend>
      <label
        v-for="option in group.options"
        :key="option.id"
        :class="{ active: modelValue[option.id] }"
        :title="option.description"
      >
        <input
          type="checkbox"
          :checked="modelValue[option.id]"
          @change="change(option.id, $event.target.checked)"
        />{{ option.label }}
      </label>
    </fieldset>
  </details>
</template>
<style scoped>
.pa-settings {
  margin: 0 0 12px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  font-size: 11px;
}
summary {
  cursor: pointer;
  color: var(--text-secondary);
}
summary span {
  color: var(--muted);
  margin-left: 8px;
  font-size: 10px;
  display: inline-block;
}
.pa-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin: 12px 0;
}
.pa-actions small {
  color: var(--muted);
  font-size: 10px;
}
fieldset {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  border: 0;
  padding: 0;
  margin: 12px 0 0;
  min-width: 0;
}
legend {
  margin-bottom: 7px;
  color: var(--muted);
}
label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px;
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  color: var(--muted);
  cursor: pointer;
}
label.active {
  background: var(--accent-soft);
  color: var(--accent-text);
  border-color: var(--accent-border);
}
input {
  margin: 0;
  accent-color: var(--accent);
}
</style>
