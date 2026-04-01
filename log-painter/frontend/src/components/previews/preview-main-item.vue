<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  source: any
  charColor: string
}>()

// 展示名的兜底
const displayName = computed(() =>
  props.source?.displayName || props.source?.who || props.source?.nickname || props.source?.name || '未知'
)

// 用去色后的内容；若父未提供则回退 message
const safeHtml = computed(() => props.source?.messageSanitized ?? props.source?.message ?? '')
</script>

<template>
  <div class="preview-item" :style="{ '--accent': charColor }">
    <div class="header">
      <span class="name" :style="{ color: 'var(--accent)' }">{{ displayName }}</span>
    </div>
    <div class="body" v-html="safeHtml"></div>
  </div>
</template>

<style scoped>
.preview-item { padding: 8px 12px; border-left: 3px solid var(--accent); }
.header { margin-bottom: 4px; font-weight: 600; }
.name { line-height: 1; }
.body :deep(img) { max-width: 100%; }
</style>
