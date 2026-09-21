<template>
  <el-card>
    <template #header>
      <span>单条病案录入 + 即时分组</span>
    </template>

    <el-form :model="form" label-width="120px" style="max-width: 720px">
      <el-form-item label="病案号">
        <el-input v-model="form.case_id" placeholder="可留空，自动生成" clearable style="width: 220px" />
      </el-form-item>
      <el-form-item label="主要诊断 (ZYZD)" required>
        <el-input v-model="form.zyzd" placeholder="如 A01.001 / I50.900" @blur="lookupZyzd" />
        <div v-if="zyzdName" class="code-preview">→ {{ zyzdName }}</div>
      </el-form-item>
      <el-form-item label="主要手术 (ZYSS)">
        <el-input v-model="form.zyss" placeholder="如 33.5100（肺移植）" @blur="lookupZyss" />
        <div v-if="zyssName" class="code-preview">→ {{ zyssName }}</div>
      </el-form-item>
      <el-form-item label="其他诊断 (QTZD)">
        <el-input
          v-model="qtzdText"
          type="textarea"
          :rows="2"
          placeholder="多个用 , 或 ; 分隔"
          @blur="lookupQtzd"
        />
        <div v-if="qtzdPreview.length" class="code-preview">
          <div v-for="p in qtzdPreview" :key="p.code">
            {{ p.code }} → {{ p.name || '（未收录）' }}
          </div>
        </div>
      </el-form-item>
      <el-form-item label="其他手术 (QTSS)">
        <el-input
          v-model="qtssText"
          type="textarea"
          :rows="2"
          placeholder="多个用 , 或 ; 分隔"
          @blur="lookupQtss"
        />
        <div v-if="qtssPreview.length" class="code-preview">
          <div v-for="p in qtssPreview" :key="p.code">
            {{ p.code }} → {{ p.name || '（未收录）' }}
          </div>
        </div>
      </el-form-item>
      <el-form-item label="入院日期">
        <el-date-picker v-model="form.admission_date" type="date" value-format="YYYY-MM-DD" placeholder="选择入院日期" style="width: 180px" />
      </el-form-item>
      <el-form-item label="出院日期">
        <el-date-picker v-model="form.discharge_date" type="date" value-format="YYYY-MM-DD" placeholder="选择出院日期" style="width: 180px" />
      </el-form-item>
      <el-form-item label="出院科室">
        <el-input v-model="form.discharge_department" placeholder="如 妇科 / 骨科二病区" clearable style="width: 220px" />
      </el-form-item>
      <el-form-item label="年龄 (NL)">
        <el-input-number v-model="form.nl" :min="0" :max="120" />
      </el-form-item>
      <el-form-item label="性别 (XB)">
        <el-select v-model="form.xb" style="width: 160px" placeholder="可选">
          <el-option :value="1" label="男" />
          <el-option :value="2" label="女" />
          <el-option :value="9" label="未知" />
        </el-select>
      </el-form-item>
      <el-form-item label="新生儿日龄">
        <el-input-number v-model="form.xsrtl" :min="0" :max="365" />
      </el-form-item>
      <el-form-item label="新生儿体重(g)">
        <el-input-number v-model="form.xsrtz" :min="0" :max="10000" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="loading" @click="submit">立即分组</el-button>
        <el-button @click="reset">重置</el-button>
      </el-form-item>
    </el-form>

    <el-divider v-if="result" />

    <div v-if="result">
      <el-result :icon="result.error_type === 'success' ? 'success' : 'warning'" :title="`DRG: ${result.drg_code}  ${result.drg_name || ''}`">
        <template #sub-title>
          <div class="result-meta">
            <el-tag size="large">MDC: {{ result.mdc_code || '-' }}<span v-if="result.mdc_name" style="margin-left: 6px; font-weight: normal">{{ result.mdc_name }}</span></el-tag>
            <el-tag size="large" type="info">ADRG: {{ result.adrg_code || '-' }}<span v-if="result.adrg_name" style="margin-left: 6px; font-weight: normal">{{ result.adrg_name }}</span></el-tag>
            <el-tag size="large" :type="ccTagType(result.cc_level)">CC: {{ result.cc_level }}</el-tag>
            <el-tag size="large" :type="result.error_type === 'success' ? 'success' : 'warning'">
              {{ result.error_type === 'success' ? '正常' : result.error_type === 'fallback' ? '兜底 0000' : '异常' }}
            </el-tag>
            <el-tag>耗时 {{ result.duration_ms }}ms</el-tag>
          </div>
          <div v-if="result.case_summary" class="result-case-info">
            <el-tag v-if="result.case_summary.admission_date || result.case_summary.discharge_date" type="info">
              {{ result.case_summary.admission_date || '?' }} → {{ result.case_summary.discharge_date || '?' }}
            </el-tag>
            <el-tag v-if="result.case_summary.discharge_department" type="info">
              {{ result.case_summary.discharge_department }}
            </el-tag>
            <el-tag v-if="result.case_id">病案号 {{ result.case_id }}</el-tag>
          </div>
        </template>
      </el-result>

      <el-button @click="goDetail" v-if="result.result_id">查看完整证据链 →</el-button>
      <EvidenceTree :evidence="result.evidence" />
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, CaseIn, GroupResult } from '@/api'
import EvidenceTree from '@/components/EvidenceTree.vue'

const router = useRouter()
const qtzdText = ref('')
const qtssText = ref('')
const form = reactive<CaseIn>({
  case_id: '',
  zyzd: '', zyss: '', qtzd_list: [], qtss_list: [],
  nl: undefined, xb: undefined, xsrtl: undefined, xsrtz: undefined,
  admission_date: '',
  discharge_date: '',
  discharge_department: '',
})
const result = ref<GroupResult | null>(null)
const loading = ref(false)

// 输入框实时查名
const zyzdName = ref('')
const zyssName = ref('')
const qtzdPreview = ref<{ code: string; name?: string | null }[]>([])
const qtssPreview = ref<{ code: string; name?: string | null }[]>([])

async function lookupZyzd() { zyzdName.value = (await api.getName(form.zyzd)).name || '' }
async function lookupZyss() { zyssName.value = form.zyss ? (await api.getName(form.zyss)).name || '' : '' }
async function lookupQtzd() {
  const codes = split(qtzdText.value)
  if (!codes.length) { qtzdPreview.value = []; return }
  const map = await api.getNames(codes)
  qtzdPreview.value = codes.map(c => ({ code: c, name: map[c] }))
}
async function lookupQtss() {
  const codes = split(qtssText.value)
  if (!codes.length) { qtssPreview.value = []; return }
  const map = await api.getNames(codes)
  qtssPreview.value = codes.map(c => ({ code: c, name: map[c] }))
}

function split(text: string): string[] {
  return text.split(/[,;]/).map(s => s.trim()).filter(Boolean)
}

async function submit() {
  if (!form.zyzd.trim()) {
    ElMessage.warning('主要诊断必填')
    return
  }
  form.qtzd_list = split(qtzdText.value)
  form.qtss_list = split(qtssText.value)
  // 清空空字符串字段，避免后端收到 ''
  const payload: CaseIn = {
    ...form,
    case_id: form.case_id?.trim() || undefined,
    admission_date: form.admission_date || undefined,
    discharge_date: form.discharge_date || undefined,
    discharge_department: form.discharge_department?.trim() || undefined,
  }
  loading.value = true
  try {
    result.value = await api.createCase(payload)
  } finally {
    loading.value = false
  }
}

function reset() {
  form.case_id = ''
  form.zyzd = form.zyss = ''
  qtzdText.value = qtssText.value = ''
  form.nl = form.xb = form.xsrtl = form.xsrtz = undefined
  form.admission_date = form.discharge_date = form.discharge_department = ''
  zyzdName.value = zyssName.value = ''
  qtzdPreview.value = qtssPreview.value = []
  result.value = null
}

function goDetail() {
  if (result.value?.result_id) {
    router.push(`/result/${result.value.result_id}`)
  }
}

function ccTagType(level: string) {
  return level === 'MCC' ? 'danger' : level === 'CC' ? 'warning' : 'info'
}
</script>

<style scoped>
.result-meta { display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; }
.result-case-info { display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; margin-top: 8px; }
.code-preview {
  font-size: 12px;
  color: #909399;
  background: #fafafa;
  padding: 4px 8px;
  border-radius: 4px;
  margin-top: 4px;
}
</style>
