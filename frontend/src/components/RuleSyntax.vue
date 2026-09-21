<template>
  <pre class="rule-syntax" v-html="highlighted"></pre>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ expr: string }>()

// 关键字高亮（顺序很重要：先匹配长串）
const RULES: Array<[RegExp, string]> = [
  [/\bnot\s+in\b/gi, 'kw-neg'],
  [/\bin\b/gi, 'kw'],
  [/\band\b/gi, 'op-and'],
  [/\bor\b/gi, 'op-or'],
  [/\bnot\b/gi, 'kw-neg'],
  [/(>=|<=|>|<|=)/g, 'op-cmp'],
  [/\b(ZYZD|ZYSS|QTZD|QTSS|QTZD_LIST|QTSS_LIST|NL|XB|XSRTL|XSRTZ)\b/g, 'var'],
  [/\b(MCC|CC)\b/g, 'cc'],
  [/\b(DI_[A-Z0-9_]+|OP_[A-Z0-9_]+|OP2_[A-Z0-9_]+)\b/g, 'set'],
  [/\b([A-Z]{2,}\d*)\b/g, 'code'],
]

const highlighted = computed(() => {
  if (!props.expr) return ''
  let html = escapeHtml(props.expr)
  // 按顺序应用替换，但要用占位符避免被后续正则吃掉
  for (const [re, cls] of RULES) {
    html = html.replace(re, (m) => `\x00${cls}\x00${escapeHtml(m)}\x00/\x00`)
  }
  // 把 \x00 还原成对应 span
  html = html.replace(/\x00([\w-]+)\x00([\s\S]*?)\x00\/\x00/g, (_, cls, body) =>
    `<span class="tk-${cls}">${body}</span>`)
  return html
})

function escapeHtml(s: string) {
  return s.replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]!))
}
</script>

<style scoped>
.rule-syntax {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 12px 16px;
  border-radius: 6px;
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}
:deep(.tk-kw) { color: #c586c0; font-weight: bold; }
:deep(.tk-kw-neg) { color: #c586c0; font-weight: bold; }
:deep(.tk-op-and) { color: #569cd6; font-weight: bold; }
:deep(.tk-op-or) { color: #569cd6; font-weight: bold; }
:deep(.tk-op-cmp) { color: #d4d4d4; }
:deep(.tk-var) { color: #9cdcfe; }
:deep(.tk-set) { color: #4ec9b0; }
:deep(.tk-cc) { color: #f48771; font-weight: bold; }
:deep(.tk-code) { color: #ce9178; }
</style>