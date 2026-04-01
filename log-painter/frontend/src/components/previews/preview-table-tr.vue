<script setup lang="ts">
const props = defineProps<{
  source?: {
    nickname?: string
    message?: string
  }
}>()
</script>

<template>
  <tr>
    <td style="padding:6px 8px;">{{ props.source?.nickname || '未知角色' }}</td>
    <td style="padding:6px 8px;">
      <span v-html="props.source?.message || ''"></span>
    </td>
  </tr>
</template>
