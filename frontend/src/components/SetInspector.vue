<template>
  <el-drawer
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    :title="`集合明细: ${setId}`"
    direction="rtl"
    size="520px"
  >
    <div v-loading="loading">
      <el-input v-model="filter" placeholder="过滤编码..." clearable style="margin-bottom: 12px" />

      <div class="meta">
        <el-tag size="small">{{ members.length }} 个编码</el-tag>
        <el-tag v-if="filtered.length !== members.length" size="small" type="info">
          过滤后 {{ filtered.length }}
        </el-tag>
        <el-button size="small" @click="copyAll" style="margin-left: auto">复制全部</el-button>
      </div>

      <div class="members-grid">
        <el-tag
          v-for="code in paged"
          :key="code"
          class="member-tag"
          effect="plain"
        >
          <code>{{ code }}</code>
          <span v-if="names[code]" class="member-name">{{ names[code] }}</span>
          <span v-else class="member-name missing">（未收录）</span>
        </el-tag>
      </div>

      <el-pagination
        v-if="filtered.length > pageSize"
        v-model:current-page="page"
        :page-size="pageSize"
        :total="filtered.length"
        layout="prev, pager, next"
        style="margin-top: 16px; justify-content: center"
      />
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '@/api'

const props = defineProps<{ modelValue: boolean; setId: string }>()
defineEmits<{ 'update:modelValue': [boolean] }>()

const loading = ref(false)
const members = ref<string[]>([])
const filter = ref('')
const page = ref(1)
const pageSize = 200
const names = ref<Record<string, string>>({})

const filtered = computed(() => {
  const q = filter.value.trim().toUpperCase()
  if (!q) return members.value
  return members.value.filter(c => c.includes(q))
})

const paged = computed(() => filtered.value.slice((page.value - 1) * pageSize, page.value * pageSize))

watch(() => props.setId, async (id) => {
  if (!id || !props.modelValue) return
  loading.value = true
  names.value = {}
  try {
    members.value = await api.getSetMembers(id)
    page.value = 1
    // 批量查名
    if (members.value.length) {
      try {
        names.value = await api.getNames(members.value)
      } catch {}
    }
  } finally {
    loading.value = false
  }
}, { immediate: true })

function copyAll() {
  navigator.clipboard.writeText(filtered.value.join('\n'))
  ElMessage.success(`已复制 ${filtered.value.length} 个编码`)
}
</script>

<style scoped>
.meta { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; }
.members-grid { display: flex; flex-wrap: wrap; gap: 6px; }
.member-tag {
  display: inline-flex;
  flex-direction: column;
  align-items: flex-start;
  padding: 4px 8px;
  font-size: 12px;
}
.member-name { color: #606266; margin-top: 2px; font-size: 11px; }
.member-name.missing { color: #c0c4cc; font-style: italic; }
</style>