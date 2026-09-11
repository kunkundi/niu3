<script setup>
import { computed } from 'vue'
import { money } from '../state'
import { PA_OPTIONS, layerItems } from '../price-action/index.js'
const props = defineProps({ result: Object, layers: Object, day: String })
const analysis = computed(() => props.result?.analysis)
const selected = computed(() => PA_OPTIONS.filter(({ id }) => props.layers[id]))
const observed = computed(() =>
  selected.value.map((option) => ({ ...option, count: layerItems(analysis.value, option.id).length })),
)
const dayIndex = computed(() => props.result?.bars.findIndex((bar) => bar.date === props.day) ?? -1)
const dayEvents = computed(() => {
  if (!analysis.value) return []
  return selected.value.flatMap((option) =>
    layerItems(analysis.value, option.id)
      .filter((item) => (item.index ?? item.startIndex ?? item.startedAtIndex) === dayIndex.value)
      .map((item) => {
        const known = item.knownAtIndex ?? item.confirmedIndex ?? item.confirmedAtIndex
        const knownDay = known == null ? null : props.result.bars[known]?.date
        const statusDay =
          item.statusKnownAtIndex == null ? null : props.result.bars[item.statusKnownAtIndex]?.date
        return `${option.label}：${item.label || item.structureLabel || item.kind || ''}${item.statusLabel ? ' · ' + item.statusLabel : ''}${knownDay ? ' · 可知于 ' + knownDay : ''}${statusDay && statusDay !== knownDay ? ' · 状态截至 ' + statusDay : ''}`
      }),
  )
})
</script>
<template>
  <div v-if="selected.length" class="pa-summary">
    <p v-if="result?.error" class="pa-note amberText">{{ result.error }}</p>
    <template v-else-if="analysis">
      <div class="pa-state">
        <span
          >结构截至 <b>{{ result.bars.at(-1).date }}</b></span
        >
        <span :title="[...analysis.marketState.evidence, ...analysis.marketState.warnings].join('；')">{{
          analysis.marketState.label
        }}</span>
        <span
          :title="
            'Always-In 表示当前方向倾向；偏多或偏空本身不触发买卖。' +
            [...analysis.alwaysIn.evidence, ...analysis.alwaysIn.warnings].join('；')
          "
          >Always-In
          <b>{{ { long: '偏多', short: '偏空', unclear: '不明' }[analysis.alwaysIn.state] }}</b></span
        >
        <span
          >ATR14 <b>{{ money(analysis.atr, 4) }}</b></span
        >
      </div>
      <p v-if="layers.higherTimeframe" class="pa-note">
        {{
          analysis.higherTimeframe.available
            ? `周 K 截至 ${analysis.higherTimeframe.sourceDate} · ${result.weeklyCount} 周 · ${analysis.higherTimeframe.marketStateLabel} · EMA20 ${money(analysis.higherTimeframe.ema20Value, 3)}`
            : analysis.higherTimeframe.warning
        }}
        日线合成，剔除首尾边界周。
      </p>
      <details>
        <summary>图层读数与标记说明</summary>
        <p class="pa-note">
          方向依据：{{ [...analysis.alwaysIn.evidence, ...analysis.alwaysIn.warnings].join('；') }}。
          趋势内整理表示近期波动收窄，但方向依据仍有效；偏多或偏空本身不触发买卖。
        </p>
        <p class="pa-note">
          当前市场状态与方向倾向使用最近 {{ result.bars.length - analysis.structureStartIndex }} 根完整日 K
          的结构。已结束的区间和窗口外的形态保留为历史标记，不参与当前状态判断。
        </p>
        <p class="pa-note">
          以下数量覆盖截至
          {{ result.bars.at(-1).date }}
          的已加载历史；当前画面只绘制范围内的标记和价位。价格行为为规则识别结果，摆动需右侧 3
          根确认；回顾模式按选中日重算结构、形态状态和周 K 背景，不使用该日之后的数据。
        </p>
        <div class="pa-day">
          <b>{{ day }}</b>
          <p>{{ dayEvents.length ? dayEvents.join('；') : '该日没有已选图层的事件标记。' }}</p>
        </div>
        <dl>
          <div v-for="option in observed" :key="option.id">
            <dt>
              {{ option.label }} <b>{{ option.count ? option.count + ' 项' : '未识别 / 不可用' }}</b>
            </dt>
            <dd>{{ option.description }}</dd>
          </div>
        </dl>
        <p class="pa-note">
          形态状态：✓ 已确认，? 待确认，× 已失败。确认时点、趋势线失效和形态生命周期沿用 NiuTwo
          规则；不能将历史标记所在日期视为当时已经确认。
        </p>
      </details>
    </template>
  </div>
</template>
<style scoped>
.pa-summary {
  padding: 10px 0;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 11px;
}
.pa-state {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  align-items: center;
}
b {
  font-weight: 500;
  color: var(--text-secondary);
}
.pa-note {
  line-height: 1.8;
  margin: 8px 0;
}
summary {
  width: fit-content;
  cursor: pointer;
  margin-top: 10px;
}
.pa-day {
  background: var(--panel2);
  padding: 10px;
  border-radius: var(--radius-control);
  line-height: 1.7;
}
.pa-day p {
  margin: 4px 0 0;
}
dl > div {
  padding: 8px 0;
  border-bottom: 1px solid var(--line);
}
dt {
  color: var(--text-secondary);
}
dt b {
  margin-left: 8px;
  color: var(--muted);
}
dd {
  margin: 4px 0 0;
  line-height: 1.7;
}
</style>
