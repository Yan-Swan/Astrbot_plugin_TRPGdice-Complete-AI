<script setup lang="ts">
const { isShow, previewItems } = defineProps<{
  isShow: boolean
  previewItems: any[]
}>()
</script>

<template>
  <pre v-if="isShow" class="preview">
    [BBS PREVIEW]\n{{ JSON.stringify(previewItems, null, 2) }}
  </pre>
</template>
