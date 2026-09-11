<script setup>
import { computed } from 'vue'
import { state, dateTime } from '../state'
import Icon from './Icon.vue'

const progress = computed(() => state.status.history_progress)
const percent = (value) => `${(Number(value) * 100).toFixed(1)}%`
const statuses = {
  preparing: '等待手动添加与资料核验',
  waiting_calendar: '等待日历确认',
  syncing: '自动补充中',
  retrying: '等待自动重试',
  cooling: '数据源冷却中',
  offline: 'Worker 离线',
  complete: '下载完成',
}
const messages = {
  preparing: '手动添加 ETF 后，后台自动获取日 K。',
  waiting_calendar: '交易日历尚未确认，暂时无法确定需要补充到哪一天。',
  syncing: '后台正在按队列补充历史数据，无需手动操作。',
  retrying: '部分标的请求失败，后台会按重试间隔继续尝试。',
  cooling: '数据源暂时限制请求，冷却结束后会自动重试。',
  offline: '未收到有效 Worker 心跳，恢复运行后会继续补充。',
  complete: '当前范围内的日 K 价格已全部更新到目标交易日。',
}
const needsAttention = computed(() =>
  ['cooling', 'retrying', 'offline', 'waiting_calendar'].includes(progress.value?.state),
)
</script>

<template>
  <section class="panel history-panel" aria-labelledby="history-progress-title">
    <div class="panel-heading">
      <div>
        <h2 id="history-progress-title">历史日 K 下载进度</h2>
        <p>手动名单及持仓中的活跃 ETF · 每 15 秒自动刷新</p>
      </div>
      <span class="chip" :class="needsAttention ? 'amber' : 'neutral'" role="status">{{
        statuses[progress?.state] || '正在读取进度'
      }}</span>
    </div>
    <template v-if="progress">
      <div class="history-summary">
        <div>
          <strong class="history-percentage">{{ percent(progress.coverage) }}</strong>
          <span>已完成 {{ progress.completed }} / {{ progress.total }} 只</span>
        </div>
        <span>覆盖门槛 {{ percent(progress.required_coverage) }}</span>
      </div>
      <div
        class="progress history-track"
        role="progressbar"
        aria-label="历史日 K 下载覆盖率"
        aria-valuemin="0"
        aria-valuemax="100"
        :aria-valuenow="Number((progress.coverage * 100).toFixed(1))"
        :aria-valuetext="`已完成 ${progress.completed} 只，共 ${progress.total} 只`"
      >
        <span :style="{ width: percent(progress.coverage) }"></span>
        <i :style="{ left: percent(progress.required_coverage) }" aria-hidden="true"></i>
      </div>
      <div class="history-stats">
        <div>
          <span>待补充</span><strong>{{ progress.pending }} <small>只</small></strong>
        </div>
        <div>
          <span>其中请求失败</span><strong>{{ progress.failed_count }} <small>只</small></strong>
        </div>
        <div>
          <span>最近 5 分钟完成</span><strong>{{ progress.recent_completed }} <small>只</small></strong>
        </div>
        <div>
          <span>距覆盖门槛</span><strong>{{ progress.remaining_to_ready }} <small>只</small></strong>
        </div>
      </div>
      <div class="history-message" :class="{ 'history-warning': needsAttention }">
        <Icon :name="needsAttention ? 'warning' : 'refresh'" :size="16" />
        <div>
          <p>{{ messages[progress.state] }}</p>
          <p v-if="progress.state === 'cooling'">
            {{ progress.cooldown_source }} · {{ dateTime(progress.cooldown_until) }} 后重试
          </p>
          <p v-if="progress.coverage_met">历史覆盖已达门槛，自动买入仍需通过其他就绪检查。</p>
          <p v-else-if="progress.total">至少完成 {{ progress.required_count }} 只后达到历史覆盖门槛。</p>
          <p v-if="progress.optional_pending">
            {{ progress.optional_pending }} 只 ETF 的成交额、换手率等辅助字段待补齐<span
              v-if="progress.optional_failed"
              >，其中 {{ progress.optional_failed }} 只接口暂不可用</span
            >；已获取的价格可继续用于策略，辅助字段独立重试。
          </p>
        </div>
      </div>
      <div class="history-footer">
        <span>目标交易日 {{ progress.target || '待确认' }}</span>
        <span>最近成功下载 {{ dateTime(progress.last_success_at) }}</span>
      </div>
      <p class="history-definition">
        已缓存历史 {{ progress.cached_count ?? 0 }} 只 · 本轮增量完成
        {{ progress.incremental_count ?? 0 }} 只。 每天 15:30 切换目标交易日，已下载的历史会继续复用。
      </p>
      <p class="history-definition">
        完成表示日 K 价格已更新到目标交易日；辅助字段缺失显示为不可用。上市不足 120 根日 K 的 ETF
        仍会被策略排除。
      </p>
    </template>
    <p v-else class="history-definition">正在获取后台下载状态…</p>
  </section>
</template>
