<template>
  <span class="rule-label">
    <code class="code">{{ code }}</code>
    <span v-if="name" class="name">{{ name }}</span>
  </span>
</template>

<script setup lang="ts">
defineProps<{
  code: string
  name?: string | null
}>()
</script>

<style scoped>
.rule-label { display: inline-flex; gap: 6px; align-items: baseline; }
.code {
  font-family: 'Consolas', 'Monaco', monospace;
  font-weight: bold;
  color: #1890ff;
}
.name { color: #606266; font-size: 0.9em; }
</style>