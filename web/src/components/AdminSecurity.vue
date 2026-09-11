<script setup>
import { onBeforeUnmount, ref } from 'vue'
import { api, state } from '../state'
import Icon from './Icon.vue'

const currentKey = ref('')
const newKey = ref('')
const confirmation = ref('')
const saving = ref(false)
const error = ref('')

function clearKeys() {
  currentKey.value = ''
  newKey.value = ''
  confirmation.value = ''
}
onBeforeUnmount(clearKeys)

async function save() {
  if (saving.value) return
  error.value = ''
  if (!newKey.value) {
    error.value = '请输入新管理密钥'
    return
  }
  if (newKey.value !== confirmation.value) {
    error.value = '两次输入的新管理密钥不一致'
    return
  }
  if (currentKey.value === newKey.value) {
    error.value = '新管理密钥不能与当前密钥相同'
    return
  }
  saving.value = true
  try {
    const result = await api('/auth/password', {
      method: 'POST',
      body: JSON.stringify({
        current_password: currentKey.value,
        new_password: newKey.value,
        confirm_password: confirmation.value,
      }),
    })
    clearKeys()
    state.authNotice = [result.message, result.warning].filter(Boolean).join('。')
    state.error = ''
    state.authenticated = false
  } catch (e) {
    error.value = e.name === 'AbortError' ? '请求超时；若已退出，请尝试使用新密钥登录' : e.message
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <section class="panel security-panel" id="admin-security" aria-labelledby="security-title">
    <div class="panel-heading">
      <div>
        <h2 id="security-title">管理密钥</h2>
        <p>修改设置页面的管理密钥，保存后所有设备需重新验证才能进入设置。</p>
      </div>
      <Icon name="shield" :size="20" />
    </div>
    <form class="security-form" @submit.prevent="save">
      <div class="security-fields">
        <label for="admin-current-key">
          <span>当前管理密钥</span>
          <input
            id="admin-current-key"
            v-model="currentKey"
            type="password"
            autocomplete="current-password"
            required
            :disabled="saving"
          />
        </label>
        <label for="admin-new-key">
          <span>新管理密钥</span>
          <input
            id="admin-new-key"
            v-model="newKey"
            type="password"
            autocomplete="new-password"
            aria-describedby="key-requirements"
            required
            :disabled="saving"
          />
        </label>
        <label for="admin-confirm-key">
          <span>确认新管理密钥</span>
          <input
            id="admin-confirm-key"
            v-model="confirmation"
            type="password"
            autocomplete="new-password"
            required
            :disabled="saving"
          />
        </label>
      </div>
      <p id="key-requirements" class="security-note">
        密钥不限制长度和字符组合。请妥善保存，修改后旧密钥立即失效。
      </p>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <div class="security-actions">
        <button class="button primary" :disabled="saving">
          <Icon name="check" :size="16" />{{ saving ? '正在修改…' : '保存密钥并重新登录' }}
        </button>
      </div>
    </form>
  </section>
</template>

<style scoped>
.security-panel {
  margin-bottom: 18px;
  scroll-margin-top: 70px;
}
.security-form {
  padding: 16px;
}
.security-fields {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.security-fields label {
  display: grid;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
}
.security-fields input {
  width: 100%;
  min-width: 0;
}
.security-note {
  color: var(--muted);
  font-size: 11px;
  line-height: 1.8;
  margin: 12px 0;
}
.security-actions {
  display: flex;
  justify-content: flex-end;
}
@media (max-width: 700px) {
  .security-panel {
    scroll-margin-top: 110px;
  }
  .security-fields {
    grid-template-columns: 1fr;
  }
  .security-actions .button {
    width: 100%;
  }
}
</style>
