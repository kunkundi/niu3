<script setup>
import { displayCode } from '../display-code.js'
import { onMounted, onUnmounted, ref } from 'vue'
import { api, money } from '../state'
import Empty from './Empty.vue'
import Icon from './Icon.vue'

const items = ref([]),
  error = ref(''),
  loading = ref(true)
let requestId = 0,
  timer
async function load() {
  const id = ++requestId
  loading.value = true
  try {
    const data = await api('/actions')
    if (id !== requestId) return
    items.value = data.items
    error.value = ''
  } catch (e) {
    if (id === requestId) error.value = e.message
  } finally {
    if (id === requestId) loading.value = false
  }
}
onMounted(() => {
  load()
  timer = setInterval(() => {
    if (!document.hidden) load()
  }, 30000)
})
onUnmounted(() => {
  requestId++
  clearInterval(timer)
})
const actionStatuses = {
  pending: '待确认权益',
  recorded: '已登记权益',
  entitled: '分红待到账',
  paid: '已到账',
  applied: '已折算',
}
</script>
<template>
  <section class="panel account-panel account-actions" aria-label="分红与折算">
    <div class="panel-heading">
      <h3>分红与折算</h3>
      <button class="icon-button" @click="load" :disabled="loading" aria-label="刷新分红与折算">
        <Icon name="refresh" :size="17" />
      </button>
    </div>
    <p class="footnote action-note">
      仅展示与账户持仓相关的分红或折算；分红按登记日持仓确定，到账日可能晚于卖出日。
    </p>
    <p v-if="error" class="form-error padded" role="status">{{ error }}</p>
    <div
      v-if="items.length"
      class="table-scroll"
      tabindex="0"
      role="region"
      aria-label="分红与折算记录，可横向滚动"
    >
      <table class="mobile-record-table">
        <thead>
          <tr>
            <th>ETF / 类型</th>
            <th>登记 / 除息日</th>
            <th>到账日</th>
            <th>每份金额 / 折算比</th>
            <th>账户权益</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in items" :key="r.id">
            <td data-label="ETF / 类型">
              {{ displayCode(r.symbol) }}<small>{{ r.kind === 'dividend' ? '现金分红' : '份额折算' }}</small>
            </td>
            <td data-label="登记 / 除息日">
              {{ r.record_day }}<small>{{ r.ex_day }}</small>
            </td>
            <td data-label="到账日">{{ r.pay_day }}</td>
            <td data-label="每份金额 / 折算比">{{ r.value }}</td>
            <td data-label="账户权益">{{ money(r.entitlement) }}</td>
            <td data-label="状态">
              <span class="chip neutral">{{
                r.verified ? actionStatuses[r.status] || r.status : '待核验'
              }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else-if="loading" class="loading" role="status">正在读取分红与折算…</div>
    <Empty
      v-else-if="!error"
      title="暂无持仓相关的分红或折算"
      description="发生与持仓相关的分红或折算后，会显示在这里。"
      icon="account"
    />
  </section>
</template>
<style scoped>
.account-actions {
  margin-bottom: 8px;
}
.panel-heading {
  padding: 8px 12px;
}
h3 {
  margin: 0;
  font-size: 13px;
}
.action-note {
  margin: 0;
  padding: 8px 12px;
}
</style>
