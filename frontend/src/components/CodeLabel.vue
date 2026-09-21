<template>
  <span class="code-label">
    <code class="code">{{ code }}</code>
    <span v-if="name" class="name">{{ name }}</span>
    <span v-else class="unknown">（未收录中文名）</span>
  </span>
</template>

<script setup lang="ts">
defineProps<{
  code: string
  name?: string | null
}>()
</script>

<style scoped>
.code-label { display: inline-flex; gap: 6px; align-items: baseline; }
.code {
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 0.9em;
  background: #f0f0f0;
  padding: 1px 6px;
  border-radius: 3px;
  color: #1890ff;
}
.name { color: #303133; }
.unknown { color: #c0c4cc; font-size: 0.85em; font-style: italic; }
</style>