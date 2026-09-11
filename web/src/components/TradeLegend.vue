<script setup>
import { TRADE_EFFECTS } from '../trade-observation.js'
</script>
<template>
  <div class="trade-effect-legend" aria-label="B/S/T 成交颜色说明">
    <span v-for="effect in TRADE_EFFECTS" :key="effect.id" :class="`trade-effect-${effect.id}`">
      <b>{{ effect.marker }}</b
      >{{ effect.label }}
    </span>
  </div>
</template>
<style scoped>
.trade-effect-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  align-items: center;
  font-size: 10px;
}
.trade-effect-legend > span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--text-secondary);
}
b {
  display: inline-grid;
  place-items: center;
  width: 16px;
  height: 16px;
  color: var(--trade-color);
  font-size: 12px;
  font-weight: 800;
}
</style>
