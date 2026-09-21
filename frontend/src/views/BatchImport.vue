<template>
  <el-card>
    <template #header>
      <span>批量分组导入</span>
    </template>

    <el-alert type="info" :closable="false" style="margin-bottom: 16px">
      <p>支持 .xlsx / .csv。需要列：<b>病案号</b>、<b>主要诊断</b>；可选列：主要手术、其他诊断（多值用 ; 或 ,）、其他手术、年龄、性别、新生儿日龄、新生儿体重、入院日期、出院日期、出院科室。</p>
      <p style="margin: 4px 0 0 0; color: #909399">日期支持 YYYY-MM-DD / YYYY/MM/DD / YYYYMMDD 或 Excel 日期格式。</p>
    </el-alert>

    <el-upload
      ref="uploadRef"
      :auto-upload="false"
      :limit="1"
      accept=".xlsx,.csv"
      :on-change="onFileChange"
    >
      <template #trigger>
        <el-button type="primary">选择文件</el-button>
      </template>
      <el-button style="margin-left: 8px" @click="downloadTemplate" type="info" plain>
        下载模板
      </el-button>
    </el-upload>

    <div v-if="file" style="margin-top: 12px">
      <el-tag>已选: {{ file.name }}</el-tag>
      <el-button type="success" @click="submit" :loading="submitting" style="margin-left: 8px">
        开始分组
      </el-button>
    </div>

    <div v-if="job" style="margin-top: 24px">
      <h3>任务进度</h3>
      <el-progress
        :percentage="Math.round(job.processed / job.total * 100)"
        :status="job.status === 'done' ? 'success' : job.status === 'failed' ? 'exception' : ''"
      />
      <div class="job-stats">
        <el-tag>总数 {{ job.total }}</el-tag>
        <el-tag type="info">已处理 {{ job.processed }}</el-tag>
        <el-tag type="success">成功 {{ job.succeeded }}</el-tag>
        <el-tag type="warning">兜底 {{ job.fallback }}</el-tag>
        <el-tag type="danger">异常 {{ job.failed }}</el-tag>
      </div>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadFile } from 'element-plus'
import { api } from '@/api'

const file = ref<File | null>(null)
const submitting = ref(false)
const job = ref<any>(null)
const pollTimer = ref<any>(null)

function onFileChange(uf: UploadFile) {
  file.value = uf.raw || null
}

async function submit() {
  if (!file.value) {
    ElMessage.warning('请先选择文件')
    return
  }
  submitting.value = true
  try {
    const res = await api.batchGroup(file.value)
    ElMessage.success(`任务已创建：${res.job_id}`)
    job.value = { job_id: res.job_id, total: res.total, processed: 0,
                 succeeded: 0, fallback: 0, failed: 0, status: 'pending' }
    startPolling(res.job_id)
  } finally {
    submitting.value = false
  }
}

function startPolling(jobId: string) {
  if (pollTimer.value) clearInterval(pollTimer.value)
  pollTimer.value = setInterval(async () => {
    const status = await api.getJob(jobId)
    job.value = status
    if (status.status === 'done' || status.status === 'failed') {
      clearInterval(pollTimer.value!)
      pollTimer.value = null
    }
  }, 1000)
}

function downloadTemplate() {
  const csv = '病案号,主要诊断,主要手术,其他诊断,其他手术,年龄,性别,入院日期,出院日期,出院科室\n' +
              'C001,A01.001,33.5100,A02.001;B99.001,,35,1,2026-01-05,2026-01-20,呼吸内科\n' +
              'C002,I50.900,,E11.900,I10.200,68,2,2026-02-10,2026-02-25,心内科二病区\n' +
              'C003,N84.001,68.2915,N93.901,E78.200,52,2,2026-03-01,2026-03-05,妇科\n'
  const blob = new Blob(['﻿' + csv], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'drg_template.csv'
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<style scoped>
.job-stats { margin-top: 12px; display: flex; gap: 8px; }
</style>
