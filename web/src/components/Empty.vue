<script setup>
import Icon from './Icon.vue'
defineProps({
  title: { type: String, default: '还没有记录' },
  description: String,
  icon: { type: String, default: 'market' },
})
</script>
<template>
  <div class="empty">
    <span class="empty-icon"><Icon :name="icon" :size="26" /></span><strong>{{ title }}</strong>
    <p v-if="description">{{ description }}</p>
  </div>
</template>
