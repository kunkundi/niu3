<script setup>
import { displayCodeText } from '../display-code.js'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, dateTime, notify } from '../state'
import Icon from './Icon.vue'
import Empty from './Empty.vue'
const props = defineProps({ active: { type: Boolean, default: true } })
const config = ref(null),
  draft = ref(null),
  picker = ref(''),
  error = ref('')
const saving = ref(false),
  tests = ref({}),
  history = ref([]),
  historyError = ref('')
const busy = computed(() => saving.value || Object.values(tests.value).some((test) => test.busy))
const added = computed(() => Object.entries(draft.value?.channels || {}).filter(([, value]) => value.added))
const statuses = {
  pending: '待发送',
  sending: '发送中',
  sent: '已发送',
  failed: '发送失败',
  cancelled: '已取消',
  unknown: '送达待确认',
}
let timer
function apply(value) {
  config.value = value
  draft.value = {
    version: value.version,
    enabled: value.enabled,
    timeout: value.timeout,
    channels: Object.fromEntries(
      Object.entries(value.channels).map(([id, channel]) => [
        id,
        {
          added: channel.added,
          enabled: channel.enabled,
          fields: Object.fromEntries(channel.fields.map((field) => [field, ''])),
          clear_signing_secret: false,
        },
      ]),
    ),
  }
  tests.value = {}
}
async function load() {
  try {
    apply(await api('/notifications/config'))
    error.value = ''
  } catch (e) {
    error.value = e.message
  }
}
async function loadHistory() {
  try {
    history.value = (await api('/notifications/history')).items
    historyError.value = ''
  } catch (e) {
    historyError.value = e.message
  }
}
onMounted(() => {
  load()
  loadHistory()
  timer = setInterval(() => {
    if (props.active && !document.hidden) loadHistory()
  }, 10000)
})
watch(
  () => props.active,
  (active) => {
    if (active) loadHistory()
  },
)
onUnmounted(() => clearInterval(timer))
function add() {
  if (!picker.value) return
  draft.value.channels[picker.value].added = true
  draft.value.channels[picker.value].enabled = true
  picker.value = ''
}
function remove(id) {
  draft.value.channels[id].added = false
  draft.value.channels[id].enabled = false
}
async function save() {
  saving.value = true
  error.value = ''
  try {
    apply(await api('/notifications/config', { method: 'PATCH', body: JSON.stringify(draft.value) }))
    notify('交易通知设置已保存，立即生效')
    await loadHistory()
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}
async function test(id) {
  tests.value[id] = { busy: true, ok: false, message: '' }
  const channel = draft.value.channels[id]
  try {
    const result = await api(`/notifications/test/${id}`, {
      method: 'POST',
      timeoutMs: 40000,
      body: JSON.stringify({
        version: draft.value.version,
        timeout: draft.value.timeout,
        fields: channel.fields,
        clear_signing_secret: channel.clear_signing_secret,
      }),
    })
    tests.value[id] = { busy: false, ok: result.ok, message: result.ok ? '测试通知已发送' : result.error }
  } catch (e) {
    tests.value[id] = {
      busy: false,
      ok: false,
      message: e.name === 'AbortError' ? '测试超时，请在通知记录中确认结果，避免重复发送' : e.message,
    }
  }
  await loadHistory()
}
</script>
<template>
  <section id="trade-notifications" class="panel notification-panel" aria-labelledby="notification-title">
    <div class="panel-heading">
      <div>
        <h2 id="notification-title">交易通知</h2>
        <p>成交入账后逐笔展示持仓变化与盈亏；同批成交合并发送。</p>
      </div>
      <span class="chip" :class="config?.enabled ? '' : 'neutral'">{{
        config?.enabled ? '已启用' : '已关闭'
      }}</span>
    </div>
    <div class="notification-body">
      <p class="form-error" v-if="error" role="alert">{{ error }}</p>
      <form v-if="draft" @submit.prevent="save">
        <fieldset :disabled="busy" class="notification-fields">
          <div class="notification-general">
            <label class="check-label"
              ><input type="checkbox" v-model="draft.enabled" />启用模拟成交通知</label
            >
            <label class="notification-timeout"
              >单次推送超时（秒）<input
                type="number"
                v-model.number="draft.timeout"
                min="1"
                max="30"
                step="1"
                required
            /></label>
          </div>
          <p class="notification-note">
            总开关关闭后不推送新成交；开启后只通知之后的成交。每条消息均标注“模拟成交，非实盘”。
            飞书使用消息卡片，钉钉、企业微信和 Telegram 使用富文本。
          </p>
          <div class="notification-add">
            <select v-model="picker" aria-label="选择通知渠道">
              <option value="">选择通知渠道</option>
              <option
                v-for="(channel, id) in config.channels"
                :key="id"
                :value="id"
                :disabled="draft.channels[id].added"
              >
                {{ channel.label }}
              </option>
            </select>
            <button class="button secondary compact" type="button" @click="add" :disabled="!picker">
              添加渠道
            </button>
          </div>
          <div class="notification-empty" v-if="!added.length">
            尚未添加通知渠道。可选飞书、钉钉、企业微信和 Telegram。
          </div>
          <div class="notification-channels">
            <article
              v-for="[id, channel] in added"
              :key="id"
              class="notification-channel"
              :aria-label="`${config.channels[id].label}通知配置`"
            >
              <div class="notification-channel-heading">
                <h3>{{ config.channels[id].label }}</h3>
                <div class="notification-channel-actions">
                  <button
                    type="button"
                    role="switch"
                    :aria-checked="channel.enabled"
                    :aria-label="`${config.channels[id].label}渠道通知`"
                    class="notification-switch"
                    :class="{ active: channel.enabled }"
                    @click="channel.enabled = !channel.enabled"
                  >
                    <span aria-hidden="true"></span>{{ channel.enabled ? '已启用' : '已关闭' }}
                  </button>
                  <button
                    type="button"
                    class="button secondary compact"
                    :aria-label="`移除${config.channels[id].label}`"
                    @click="remove(id)"
                  >
                    移除
                  </button>
                </div>
              </div>
              <label class="notification-field" v-for="field in config.channels[id].fields" :key="field">
                <span>{{ config.field_labels[field] }}</span>
                <input
                  type="password"
                  v-model="channel.fields[field]"
                  :aria-label="`${config.channels[id].label} ${config.field_labels[field]}`"
                  autocomplete="new-password"
                  spellcheck="false"
                  maxlength="2048"
                  :disabled="field === 'signing_secret' && channel.clear_signing_secret"
                  :placeholder="config.channels[id].configured[field] ? '已设置，留空保留' : '未设置'"
                />
                <small>{{
                  config.channels[id].configured[field]
                    ? '已保存 · 不回显'
                    : field === 'signing_secret'
                      ? '机器人未启用签名校验时可留空'
                      : '请填写该渠道的目标配置'
                }}</small>
              </label>
              <label
                class="check-label"
                v-if="
                  config.channels[id].fields.includes('signing_secret') &&
                  config.channels[id].configured.signing_secret
                "
                ><input type="checkbox" v-model="channel.clear_signing_secret" />保存时清除签名密钥</label
              >
              <div class="notification-test">
                <button type="button" class="button secondary compact" @click="test(id)">
                  {{ tests[id]?.busy ? '发送中…' : '发送测试通知' }}
                </button>
                <span class="notification-note">仅测试此渠道，不保存配置</span>
              </div>
              <p
                v-if="tests[id]?.message"
                class="notification-test-result"
                :class="{ failure: !tests[id].ok }"
                role="status"
              >
                {{ tests[id].message }}
              </p>
            </article>
          </div>
          <p class="notification-note">
            关闭渠道会保留配置；移除并保存后清除该渠道配置。敏感字段留空表示保留旧值。测试优先使用当前输入，不受总开关和渠道开关影响。
          </p>
        </fieldset>
        <div class="notification-save">
          <span class="notification-note">通知配置 v{{ config.version }} · 保存后立即生效</span>
          <div>
            <button type="button" class="button secondary" @click="load" :disabled="busy">重新加载</button
            ><button class="button primary" :disabled="busy">
              <Icon name="check" :size="16" />{{ saving ? '保存中…' : '保存通知设置' }}
            </button>
          </div>
        </div>
      </form>
      <button v-else class="button secondary" @click="load">重新加载通知设置</button>
    </div>
    <div class="panel-heading notification-history-heading">
      <div>
        <h2>通知记录</h2>
        <p>最近 50 条发送结果 · 失败或结果不确定时不会自动重发</p>
      </div>
      <button type="button" class="icon-button" aria-label="刷新通知记录" @click="loadHistory">
        <Icon name="refresh" :size="18" />
      </button>
    </div>
    <p class="form-error padded" v-if="historyError" role="alert">{{ historyError }}</p>
    <div class="table-scroll" v-if="history.length">
      <table>
        <thead>
          <tr>
            <th>时间 / 渠道</th>
            <th>类型</th>
            <th>状态</th>
            <th>消息与结果</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in history" :key="row.id">
            <td>
              {{ dateTime(row.at)
              }}<small>{{ config?.channels[row.channel]?.label || row.channel }} · #{{ row.id }}</small>
            </td>
            <td>{{ row.kind === 'test' ? '测试通知' : '成交通知' }}</td>
            <td>
              <span
                class="chip"
                :class="
                  row.status === 'sent'
                    ? ''
                    : ['failed', 'unknown'].includes(row.status)
                      ? 'amber'
                      : 'neutral'
                "
                >{{ statuses[row.status] }}</span
              >
            </td>
            <td class="reason-cell">
              <span v-if="row.error">{{ row.error }}</span>
              <details>
                <summary>查看通知内容</summary>
                <p class="notification-message">{{ displayCodeText(row.message) }}</p>
              </details>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <Empty
      v-else
      title="暂无通知记录"
      description="添加渠道后可发送测试通知；实际成交通知在启用后的新成交入账时生成。"
    />
  </section>
</template>
