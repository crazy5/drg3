<template>
  <el-card v-loading="loading">
    <template #header>
      <span>结果详情 #{{ id }}</span>
    </template>

    <div v-if="result">
      <el-page-header @back="$router.back()" :icon="ArrowLeft" content="返回" />

      <el-descriptions :column="3" border style="margin-top: 16px">
        <el-descriptions-item label="病案号">{{ result.case_id }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="result.error_type === 'success' ? 'success' : 'warning'">
            {{ statusText }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="耗时">{{ result.duration_ms }}ms</el-descriptions-item>

        <el-descriptions-item label="MDC">
          <RuleLabel :code="result.mdc_code || '-'" :name="result.mdc_name" />
        </el-descriptions-item>
        <el-descriptions-item label="ADRG">
          <RuleLabel :code="result.adrg_code || '-'" :name="result.adrg_name" />
        </el-descriptions-item>
        <el-descriptions-item label="DRG">
          <el-tag size="large" :type="result.error_type === 'success' ? 'success' : 'warning'">
            {{ result.drg_code }}
            <span v-if="result.drg_name" style="margin-left: 6px; font-weight: normal">
              {{ result.drg_name }}
            </span>
          </el-tag>
        </el-descriptions-item>

        <el-descriptions-item label="CC 等级">
          <el-tag :type="result.cc_level === 'MCC' ? 'danger' : result.cc_level === 'CC' ? 'warning' : 'info'">
            {{ result.cc_level }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item v-if="cs?.nl != null" label="年龄">
          {{ cs.nl }} 岁
        </el-descriptions-item>
        <el-descriptions-item v-if="cs?.xb != null" label="性别">
          {{ xbText }}
        </el-descriptions-item>
        <el-descriptions-item v-if="cs?.nl == null && cs?.xb == null" label="人口学">
          —
        </el-descriptions-item>

        <el-descriptions-item label="主诊断" :span="3">
          <CodeLabel v-if="cs?.zyzd?.code" :code="cs.zyzd.code" :name="cs.zyzd.name" />
          <span v-else>{{ result.evidence.case_summary?.zyzd || '-' }}</span>
        </el-descriptions-item>

        <el-descriptions-item v-if="cs?.zyss?.code" label="主手术" :span="3">
          <CodeLabel :code="cs.zyss.code" :name="cs.zyss.name" />
        </el-descriptions-item>

        <el-descriptions-item v-if="cs?.qtzd_list?.length" label="其他诊断" :span="3">
          <div style="display: flex; flex-wrap: wrap; gap: 12px">
            <CodeLabel
              v-for="c in cs.qtzd_list"
              :key="c.code"
              :code="c.code"
              :name="c.name"
            />
          </div>
        </el-descriptions-item>
        <el-descriptions-item v-if="cs?.qtss_list?.length" label="其他手术" :span="3">
          <div style="display: flex; flex-wrap: wrap; gap: 12px">
            <CodeLabel
              v-for="c in cs.qtss_list"
              :key="c.code"
              :code="c.code"
              :name="c.name"
            />
          </div>
        </el-descriptions-item>

        <el-descriptions-item v-if="cs?.admission_date" label="入院日期">
          {{ cs.admission_date }}
        </el-descriptions-item>
        <el-descriptions-item v-if="cs?.discharge_date" label="出院日期">
          {{ cs.discharge_date }}
        </el-descriptions-item>
        <el-descriptions-item v-if="cs?.discharge_department" label="出院科室">
          {{ cs.discharge_department }}
        </el-descriptions-item>

        <el-descriptions-item v-if="cs?.xsrtl != null" label="新生儿日龄">
          {{ cs.xsrtl }} 天
        </el-descriptions-item>
        <el-descriptions-item v-if="cs?.xsrtz != null" label="新生儿出生体重">
          {{ cs.xsrtz }} g
        </el-descriptions-item>
      </el-descriptions>

      <EvidenceTree :evidence="result.evidence" />
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowLeft } from '@element-plus/icons-vue'
import { api } from '@/api'
import CodeLabel from '@/components/CodeLabel.vue'
import RuleLabel from '@/components/RuleLabel.vue'
import EvidenceTree from '@/components/EvidenceTree.vue'

const route = useRoute()
const id = Number(route.params.id)
const result = ref<any>(null)
const loading = ref(true)

const statusText = computed(() => {
  if (result.value?.error_type === 'success') return '正常'
  if (result.value?.error_type === 'fallback') return '兜底 0000'
  return result.value?.error_type || '-'
})

// case_summary 引用：后端 _summary() 已包含所有 11 个字段（编码+中文名+人口学+日期+科室）
const cs = computed(() => result.value?.case_summary)

// 性别编码转换：1=男 2=女 9=未知（GB/T 2261.1）
const xbText = computed(() => {
  const x = cs.value?.xb
  if (x === 1) return '男'
  if (x === 2) return '女'
  if (x === 9) return '未知'
  return '—'
})

onMounted(async () => {
  try {
    result.value = await api.getResult(id)
  } finally {
    loading.value = false
  }
})
</script>
