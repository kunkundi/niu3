<script setup>
import { displayCode } from '../display-code.js'
import { ref } from 'vue'
import { api } from '../state'
import { etfInputCode } from '../etf-suggestions'
import EtfAutocomplete from './EtfAutocomplete.vue'
import WorkbenchDialog from './WorkbenchDialog.vue'

const emit = defineEmits(['close', 'added'])
const query = ref(''),
  selected = ref(null),
  adding = ref(false),
  error = ref('')

function select(item) {
  selected.value = item
  error.value = ''
}
async function confirmAdd() {
  // Only an explicitly selected suggestion can be submitted, never free text.
  const item = selected.value
  if (adding.value || !item || item.watched || query.value !== item.symbol) return
  const code = etfInputCode(item.symbol)
  if (!code) return
  adding.value = true
  error.value = ''
  try {
    const result = await api('/etfs', {
      method: 'POST',
      body: JSON.stringify({ code }),
      timeoutMs: 30000,
    })
    emit('added', result)
  } catch (e) {
    error.value = e.name === 'AbortError' ? '添加请求超时，请刷新列表确认结果后重试' : e.message
  } finally {
    adding.value = false
  }
}
</script>

<template>
  <WorkbenchDialog
    :open="true"
    title="添加 ETF"
    class="etf-add-dialog"
    :close-disabled="adding"
    @update:open="(open) => !open && !adding && emit('close')"
  >
    <div class="etf-add-content" :aria-busy="adding">
      <p class="add-description">搜索并选择 ETF，核对名称和代码后确认添加到我的名单。</p>
      <label for="manual-etf-code">查找要添加的 ETF</label>
      <EtfAutocomplete v-model="query" :disabled="adding" @select="select" />
      <div v-if="selected" class="etf-add-selection" role="status">
        <span>待添加 ETF</span>
        <strong>{{ displayCode(selected.name) }}</strong>
        <small
          >{{ selected.symbol.startsWith('sh') ? '沪市' : '深市' }} ·
          {{ displayCode(selected.symbol) }}</small
        >
      </div>
      <p v-else class="add-hint">请从搜索结果中选择一只 ETF。</p>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <div class="etf-add-actions">
        <button type="button" class="button secondary" :disabled="adding" @click="emit('close')">取消</button>
        <button
          type="button"
          class="button primary"
          :disabled="adding || !selected || selected.watched"
          @click="confirmAdd"
        >
          {{ adding ? '正在添加…' : '确认添加' }}
        </button>
      </div>
    </div>
  </WorkbenchDialog>
</template>

<style scoped>
.etf-add-dialog {
  width: min(560px, calc(100vw - 24px));
}
.etf-add-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.add-description,
.add-hint {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.7;
}
label {
  color: var(--ink);
  font-size: 12px;
  font-weight: 600;
}
.etf-add-selection {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 14px;
  border: 1px solid var(--accent-border);
  border-radius: var(--radius-control);
  background: var(--accent-soft);
  overflow-wrap: anywhere;
}
.etf-add-selection span,
.etf-add-selection small {
  color: var(--muted);
  font-size: 12px;
}
.etf-add-selection strong {
  color: var(--ink);
}
.etf-add-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding-top: 8px;
}
.form-error {
  margin: 0;
}
@media (max-width: 700px) {
  .etf-add-dialog {
    width: 100%;
  }
  .etf-add-actions {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }
}
</style>
