<script setup lang="ts">
import { useStore } from '../../store'
import PreviewItem from './preview-main-item.vue'

const props = defineProps<{
  isShow: boolean
  previewItems: any[]
}>()

const store = useStore()

function resolveColor(it: any) {
  const who = it?.who || it?.nickname || it?.name || ''
  const id  = it?.IMUserId || ''
  // 优先 ID+名字全匹配，退化为仅名字匹配
  let p = store.pcList.find(x => x.name === who && x.IMUserId === id)
  if (!p) p = store.pcList.find(x => x.name === who)
  return p?.color || '#8884ff'
}
</script>

<template>
  <div v-if="isShow" class="preview-main">
    <PreviewItem
      v-for="it in previewItems"
      :key="String(it.index) + ':' + resolveColor(it)"
      :source="it"
      :char-color="resolveColor(it)"
    />
  </div>
</template>
