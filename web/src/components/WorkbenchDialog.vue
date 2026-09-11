<script setup>
import { onMounted, ref, watch } from 'vue'
const props = defineProps({ open: Boolean, title: String, closeDisabled: Boolean })
const emit = defineEmits(['update:open'])
const dialog = ref(null)
function sync() {
  if (!dialog.value) return
  if (props.open && !dialog.value.open) dialog.value.showModal()
  else if (!props.open && dialog.value.open) dialog.value.close()
}
watch(() => props.open, sync, { flush: 'post' })
onMounted(sync)
</script>

<template>
  <dialog
    ref="dialog"
    class="workbench-dialog"
    :aria-label="title"
    @cancel="(event) => closeDisabled && event.preventDefault()"
    @close="emit('update:open', false)"
  >
    <header>
      <h2>{{ title }}</h2>
      <button
        class="button secondary compact"
        :disabled="closeDisabled"
        autofocus
        @click="emit('update:open', false)"
      >
        关闭
      </button>
    </header>
    <div class="dialog-body"><slot /></div>
  </dialog>
</template>

<style scoped>
.workbench-dialog {
  position: fixed;
  inset: 0;
  width: min(960px, calc(100vw - 24px));
  max-height: calc(100dvh - 24px);
  margin: auto;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius-card);
  background: var(--bg);
  color: var(--text-secondary);
  overflow: auto;
}
.workbench-dialog::backdrop {
  background: rgb(0 0 0 / 55%);
}
header {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--line);
  background: var(--panel);
}
.dialog-body {
  padding: 14px 16px;
}
@media (max-width: 700px) {
  .workbench-dialog {
    width: 100%;
    max-width: 100%;
    max-height: calc(100dvh - max(12px, env(safe-area-inset-top)));
    margin: auto 0 0;
    border-radius: 14px 14px 0 0;
    overscroll-behavior: contain;
  }
  header {
    padding: 8px 12px;
  }
  header h2 {
    min-width: 0;
    overflow-wrap: anywhere;
    font-size: 15px;
  }
  .dialog-body {
    padding: 12px 12px max(16px, env(safe-area-inset-bottom));
    font-size: 13px;
    min-width: 0;
    overflow-wrap: anywhere;
  }
}
</style>
