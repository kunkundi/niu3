<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import Icon from './components/Icon.vue'
import ThemeToggle from './components/ThemeToggle.vue'
import WorkbenchDialog from './components/WorkbenchDialog.vue'
import logoUrl from './assets/niuno3-logo.svg'
import { preventTouchMenu } from './chart-pointer'
import { api, refresh, state, notify, completeAuthentication, cancelAuthentication } from './state'
const route = useRoute()
const password = ref(''),
  loginError = ref(''),
  loginBusy = ref(false),
  now = ref(new Date())
watch(
  () => state.authRequested,
  () => {
    password.value = ''
    loginError.value = ''
  },
)
watch(() => route.fullPath, cancelAuthentication)
const clockLabel = computed(() =>
  now.value.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }),
)
const links = [
  { path: '/', icon: 'overview', label: '投资总览' },
  { path: '/market', icon: 'market', label: '我的 ETF' },
  { path: '/signals', icon: 'signals', label: '策略信号' },
  { path: '/settings', icon: 'settings', label: '设置与运行' },
]
const statusLabel = computed(() =>
  !state.checked || !state.status.at ? '连接中' : state.status.automation?.label || '正在读取运行状态',
)
let interval,
  clockInterval,
  inFlight = false
async function poll() {
  if (inFlight || document.hidden) return
  inFlight = true
  try {
    await refresh()
  } finally {
    inFlight = false
  }
}
onMounted(async () => {
  clockInterval = setInterval(() => (now.value = new Date()), 1000)
  try {
    state.authenticated = (await api('/auth/session')).authenticated
  } catch (e) {
    state.error = e.message
  }
  state.checked = true
  await poll()
  interval = setInterval(poll, 15000)
  document.addEventListener('visibilitychange', poll)
})
onUnmounted(() => {
  cancelAuthentication()
  clearInterval(interval)
  clearInterval(clockInterval)
  document.removeEventListener('visibilitychange', poll)
})
async function login() {
  if (loginBusy.value) return
  loginBusy.value = true
  loginError.value = ''
  try {
    await api('/auth/login', { method: 'POST', body: JSON.stringify({ password: password.value }) })
    completeAuthentication()
    password.value = ''
  } catch (e) {
    loginError.value = e.message
  } finally {
    loginBusy.value = false
  }
}
async function logout() {
  try {
    await api('/auth/logout', { method: 'POST' })
    state.authenticated = false
  } catch (e) {
    notify(e.message)
  }
}
</script>
<template>
  <div
    class="app-shell"
    :class="{ 'signal-screen': route.path === '/signals', 'overview-screen': route.path === '/' }"
    @contextmenu="preventTouchMenu"
  >
    <a class="skip-link" href="#main-content">跳至主要内容</a>
    <header class="terminal-header">
      <RouterLink to="/" class="brand" aria-label="牛牛3号 投资总览">
        <img
          class="brand-mark"
          :src="logoUrl"
          width="32"
          height="32"
          alt=""
          aria-hidden="true"
          draggable="false"
        />
        <strong>牛牛3号</strong>
      </RouterLink>
      <nav aria-label="主导航" class="terminal-nav">
        <RouterLink
          v-for="item in links"
          :key="item.path"
          :to="item.path"
          :class="{ active: route.path === item.path }"
          :aria-current="route.path === item.path ? 'page' : undefined"
          ><Icon :name="item.icon" :size="17" /><span>{{ item.label }}</span></RouterLink
        >
      </nav>
      <div class="terminal-session">
        <span class="environment-label">模拟交易</span>
        <ThemeToggle />
        <button
          class="icon-button"
          v-if="state.authenticated"
          @click="logout"
          aria-label="退出登录"
          title="退出登录"
        >
          <Icon name="logout" :size="17" />
        </button>
      </div>
    </header>
    <div class="workspace">
      <main id="main-content">
        <div class="page-heading">
          <div class="page-title">
            <h1>{{ route.meta.title }}</h1>
          </div>
          <div class="heading-actions">
            <span
              class="status-pill"
              :title="state.status.automation?.message"
              :class="{
                warning: state.status.ready === false || !!state.error,
              }"
              ><i></i>{{ statusLabel }}</span
            >
          </div>
        </div>
        <div class="notice error" role="alert" v-if="state.error">
          <Icon name="warning" :size="18" />{{ state.error }}
        </div>
        <div v-if="!state.checked" class="panel loading">正在连接…</div>
        <section class="login-layout" v-else-if="route.path === '/settings' && !state.authenticated">
          <form class="panel login-card" @submit.prevent="login">
            <h2>验证管理密码</h2>
            <p>验证后可管理策略参数、通知并查看运行记录。</p>
            <p v-if="state.authNotice" role="status">{{ state.authNotice }}</p>
            <label for="password">管理密码</label
            ><input
              id="password"
              v-model="password"
              type="password"
              autocomplete="current-password"
              required
              placeholder="输入管理密码"
            />
            <p class="form-error" v-if="loginError" role="alert">{{ loginError }}</p>
            <button class="button primary" :disabled="loginBusy">
              {{ loginBusy ? '正在验证…' : '进入设置' }}<Icon name="arrow" :size="18" />
            </button>
            <RouterLink to="/market" class="text-link"
              >先浏览 我的 ETF <Icon name="arrow" :size="15"
            /></RouterLink>
          </form>
        </section>
        <RouterView v-else />
      </main>
    </div>
    <footer class="terminal-statusbar">
      <div>
        <span class="status-dot" :class="{ warning: !state.status.session_open }"></span
        >{{ state.status.at ? (state.status.session_open ? '交易时段' : '非交易时段') : '连接中'
        }}<span class="statusbar-divider"></span><span>沪深 ETF</span>
      </div>
      <div>
        <span class="data-label">行情以标注时间为准</span><time>{{ clockLabel }}</time
        ><span class="timezone">UTC+8</span>
      </div>
    </footer>
    <div class="toast" role="status" v-if="state.toast">
      <Icon name="check" :size="18" />{{ state.toast }}
    </div>
    <WorkbenchDialog
      :open="state.authRequested"
      title="验证管理密码"
      @update:open="(open) => !open && cancelAuthentication()"
    >
      <form class="panel login-card" @submit.prevent="login">
        <p>输入管理密码以继续操作。</p>
        <p v-if="state.authNotice" role="status">{{ state.authNotice }}</p>
        <label for="action-password">管理密码</label>
        <input
          id="action-password"
          v-model="password"
          type="password"
          autocomplete="current-password"
          required
          :disabled="loginBusy"
        />
        <p v-if="loginError" class="form-error" role="alert">{{ loginError }}</p>
        <button class="button primary" :disabled="loginBusy">
          {{ loginBusy ? '正在验证…' : '验证并继续操作' }}
        </button>
      </form>
    </WorkbenchDialog>
  </div>
</template>
