<template>
  <div v-loading="loading">
    <!-- 筛选条 -->
    <el-card style="margin-bottom: 16px">
      <el-form inline :model="filters" label-width="auto">
        <el-form-item label="出院日期">
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            range-separator="→"
            start-placeholder="开始"
            end-placeholder="结束"
            value-format="YYYY-MM-DD"
            unlink-panels
            style="width: 260px"
            @change="load"
          />
        </el-form-item>
        <el-form-item label="出院科室">
          <el-select
            v-model="filters.discharge_department"
            placeholder="全部科室"
            clearable
            filterable
            style="width: 200px"
            @change="load"
          >
            <el-option v-for="d in departments" :key="d" :value="d" :label="d" />
          </el-select>
        </el-form-item>
        <el-form-item label="DRG">
          <el-select
            v-model="filters.drg_code"
            placeholder="全部"
            clearable
            filterable
            style="width: 220px"
            @change="load"
          >
            <el-option v-for="d in allDrgCodes" :key="d.code" :value="d.code" :label="`${d.code} ${d.name || ''}`" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button @click="clearFilters">清空</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-row :gutter="16">
      <el-col :span="6" v-for="card in cards" :key="card.label">
        <el-card shadow="hover">
          <div class="stat-card">
            <div class="stat-value" :style="{ color: card.color }">{{ card.value }}</div>
            <div class="stat-label">{{ card.label }}</div>
            <div class="stat-sub">{{ card.sub }}</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 16px">
      <el-col :span="12">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>DRG 病例数分布</span>
              <div>
                <el-input v-model="drgFilter" size="small" placeholder="过滤 DRG / 名称..." clearable style="width: 180px" />
                <span style="margin-left: 8px; color: #909399; font-size: 12px">点击行查看病例</span>
              </div>
            </div>
          </template>
          <el-table :data="filteredTopDrgs" stripe height="380" style="cursor: pointer" @row-click="openDrgCases">
            <el-table-column type="index" width="48" />
            <el-table-column label="DRG">
              <template #default="{ row }">
                <strong>{{ row.drg_code }}</strong>
                <span v-if="row.drg_name" style="margin-left: 6px; color: #606266">{{ row.drg_name }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="count" label="条数" width="80" sortable />
            <el-table-column label="占比">
              <template #default="{ row }">{{ pct(row.count, totalCount) }}%</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>MDC 分布</span>
              <span style="color: #909399; font-size: 12px">点击行查看病例</span>
            </div>
          </template>
          <el-table :data="mdcDist" stripe height="380" style="cursor: pointer" @row-click="openMdcCases">
            <el-table-column prop="mdc_code" label="MDC" width="120" />
            <el-table-column label="MDC 名称">
              <template #default="{ row }">{{ row.mdc_name || '-' }}</template>
            </el-table-column>
            <el-table-column prop="count" label="条数" width="100" sortable />
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 16px">
      <el-col :span="8">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>兜底率监控</span>
              <span v-if="fallbacks.length" style="color: #f56c6c; font-size: 12px">
                ⚠ {{ fallbacks.length }} 条未分组
              </span>
            </div>
          </template>
          <div class="fallback-chart">
            <el-progress
              type="dashboard"
              :percentage="fallbackRate"
              :color="fallbackRate > 5 ? '#f56c6c' : '#67c23a'"
            />
            <p v-if="fallbackRate > 5" style="color: #f56c6c; margin-top: 12px; font-size: 12px">
              兜底率 > 5%，建议检查规则导入
            </p>
          </div>
          <!-- 兜底下钻：列出未分组的病案，点击看 evidence -->
          <el-table
            v-if="fallbacks.length"
            :data="fallbacks"
            stripe
            size="small"
            :show-header="false"
            height="200"
            style="margin-top: 12px"
            @row-click="goDetail"
          >
            <el-table-column label="病案" width="150">
              <template #default="{ row }">
                <code style="font-size: 12px">{{ row.case_id }}</code>
              </template>
            </el-table-column>
            <el-table-column label="主诊断">
              <template #default="{ row }">
                <span style="font-size: 12px">
                  <strong>{{ row.case_summary?.zyzd?.code || '-' }}</strong>
                  <span v-if="row.case_summary?.zyzd?.name" style="color: #909399; margin-left: 4px">
                    {{ row.case_summary.zyzd.name }}
                  </span>
                  <span v-else style="color: #c0c4cc; margin-left: 4px">（未收录）</span>
                </span>
              </template>
            </el-table-column>
          </el-table>
          <p v-else style="color: #67c23a; text-align: center; margin-top: 16px; font-size: 12px">
            ✓ 当前筛选下无兜底病案
          </p>
        </el-card>
      </el-col>
      <el-col :span="16">
        <el-card>
          <template #header>最近结果</template>
          <el-table :data="recent" @row-click="goDetail" stripe height="380">
            <el-table-column prop="case_id" label="病案号" width="100" />
            <el-table-column label="出院日期" width="110">
              <template #default="{ row }">
                {{ row.case_summary?.discharge_date || '-' }}
              </template>
            </el-table-column>
            <el-table-column label="科室" width="130">
              <template #default="{ row }">
                {{ row.case_summary?.discharge_department || '-' }}
              </template>
            </el-table-column>
            <el-table-column label="DRG" width="220">
              <template #default="{ row }">
                <strong>{{ row.drg_code }}</strong>
                <span v-if="row.drg_name" style="margin-left: 6px; color: #606266">{{ row.drg_name }}</span>
              </template>
            </el-table-column>
            <el-table-column label="MDC" width="170">
              <template #default="{ row }">
                {{ row.mdc_code }}
                <span v-if="row.mdc_name" style="margin-left: 6px; color: #909399; font-size: 12px">{{ row.mdc_name }}</span>
              </template>
            </el-table-column>
            <el-table-column label="CC" width="80">
              <template #default="{ row }">
                <el-tag :type="ccTag(row.cc_level)" size="small">{{ row.cc_level }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="主诊断">
              <template #default="{ row }">
                <template v-if="row.case_summary">
                  <code style="color: #1890ff">{{ row.case_summary.zyzd.code }}</code>
                  <span v-if="row.case_summary.zyzd.name" style="margin-left: 6px">{{ row.case_summary.zyzd.name }}</span>
                </template>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <!-- 下钻 dialog：点击 DRG/MDC 行 → 列出该组下的所有病案 -->
    <el-dialog
      v-model="drillDialog.visible"
      :title="drillDialog.title"
      width="92%"
      top="5vh"
      destroy-on-close
      @closed="drillDialog.cases = []"
    >
      <el-table
        v-loading="drillDialog.loading"
        :data="drillDialog.cases"
        stripe
        height="540"
        style="cursor: pointer"
        @row-click="goDetailFromDrill"
      >
        <el-table-column prop="case_id" label="病案号" width="150" />
        <el-table-column label="出院日期" width="110">
          <template #default="{ row }">{{ row.case_summary?.discharge_date || '-' }}</template>
        </el-table-column>
        <el-table-column label="科室" width="130">
          <template #default="{ row }">{{ row.case_summary?.discharge_department || '-' }}</template>
        </el-table-column>
        <el-table-column label="DRG" width="200">
          <template #default="{ row }">
            <strong>{{ row.drg_code }}</strong>
            <span v-if="row.drg_name" style="margin-left: 4px; color: #606266; font-size: 12px">{{ row.drg_name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="ADRG" width="170">
          <template #default="{ row }">
            {{ row.adrg_code }}
            <span v-if="row.adrg_name" style="margin-left: 4px; color: #909399; font-size: 12px">{{ row.adrg_name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="MDC" width="170">
          <template #default="{ row }">
            {{ row.mdc_code }}
            <span v-if="row.mdc_name" style="margin-left: 4px; color: #909399; font-size: 12px">{{ row.mdc_name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="CC" width="70">
          <template #default="{ row }">
            <el-tag :type="ccTag(row.cc_level)" size="small">{{ row.cc_level }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="主诊断" min-width="220">
          <template #default="{ row }">
            <code style="color: #1890ff">{{ row.case_summary?.zyzd?.code || '-' }}</code>
            <span v-if="row.case_summary?.zyzd?.name" style="margin-left: 4px">{{ row.case_summary.zyzd.name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="主手术" min-width="200">
          <template #default="{ row }">
            <template v-if="row.case_summary?.zyss?.code">
              <code style="color: #1890ff">{{ row.case_summary.zyss.code }}</code>
              <span v-if="row.case_summary.zyss.name" style="margin-left: 4px">{{ row.case_summary.zyss.name }}</span>
            </template>
            <span v-else style="color: #c0c4cc">—</span>
          </template>
        </el-table-column>
      </el-table>
      <p style="margin-top: 12px; color: #909399; font-size: 12px">
        共 {{ drillDialog.cases.length }} 条；点击行进入病案详情（含完整入组证据链）
      </p>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api'

const router = useRouter()
const loading = ref(true)
const totalCount = ref(0)              // 当前筛选下的总病案数（模板里算占比用）

const cards = ref<any[]>([])
const topDrgs = ref<any[]>([])
const mdcDist = ref<any[]>([])
const recent = ref<any[]>([])
const fallbacks = ref<any[]>([])      // 兜底（drg_code=0000）明细
const fallbackRate = ref(0)
const drgFilter = ref('')

const departments = ref<string[]>([])
const allDrgCodes = ref<{ code: string; name?: string | null }[]>([])

const dateRange = ref<[string, string] | null>(null)

// 「最近上一个月」范围：默认展示上个月 1 号到月底
// 例：今天 2026-09-21 → 默认 [2026-08-01, 2026-08-31]
function lastMonthRange(): [string, string] {
  const now = new Date()
  const y = now.getFullYear()
  const m = now.getMonth()      // 0-based：本月
  // 上个月所在年/月（1月时回退到去年 12 月）
  const ly = m === 0 ? y - 1 : y
  const lm = m === 0 ? 12 : m
  const first = new Date(ly, lm - 1, 1)
  const last = new Date(ly, lm, 0)        // 第 0 天 = 上月最后一天
  // 用本地年月日拼，不要用 toISOString（UTC 会把本地 8-1 推到 7-31）
  const fmt = (d: Date) => {
    const yyyy = d.getFullYear()
    const mm = String(d.getMonth() + 1).padStart(2, '0')
    const dd = String(d.getDate()).padStart(2, '0')
    return `${yyyy}-${mm}-${dd}`
  }
  return [fmt(first), fmt(last)]
}
dateRange.value = lastMonthRange()
const filters = reactive<{
  discharge_department?: string
  drg_code?: string
}>({
  discharge_department: undefined,
  drg_code: undefined,
})

// 下钻 dialog
const drillDialog = reactive({
  visible: false,
  loading: false,
  title: '',
  filter: {} as { drg_code?: string; mdc_code?: string },
  cases: [] as any[],
})

const filteredTopDrgs = computed(() => {
  const q = drgFilter.value.trim().toUpperCase()
  if (!q) return topDrgs.value
  return topDrgs.value.filter(
    r => r.drg_code.includes(q) || (r.drg_name || '').toUpperCase().includes(q),
  )
})

// 公共参数（日期/科室筛）
function _baseParams(): Record<string, string> {
  const p: Record<string, string> = {}
  if (dateRange.value?.[0]) p.discharge_date_from = dateRange.value[0]
  if (dateRange.value?.[1]) p.discharge_date_to = dateRange.value[1]
  if (filters.discharge_department) p.discharge_department = filters.discharge_department
  return p
}

async function load() {
  loading.value = true
  try {
    const base = _baseParams()

    // 走 4 个 stats 接口 + 1 个最近结果列表，并发
    // （总数、error 分布、DRG 分布、MDC 分布都是后端 SQL 聚合，永远精确）
    const [cnt, errRate, drgDist, mdcDistRes, recentList] = await Promise.all([
      api.statsCount(base),
      api.statsErrorRate(base),
      api.statsDistribution({ ...base, by: 'drg' }),
      api.statsDistribution({ ...base, by: 'mdc' }),
      api.listResults({ ...base, limit: 20 }).catch(() => []),
    ])

    const total = cnt.total || 0
    totalCount.value = total
    const success = errRate.success || 0
    const fallback = errRate.fallback || 0
    const failed = errRate.error || 0
    const avg = recentList.length
      ? (recentList.reduce((s: number, r: any) => s + (r.duration_ms || 0), 0) / recentList.length).toFixed(1)
      : 0

    cards.value = [
      { label: '总病案数', value: total, sub: '本次筛选', color: '#1890ff' },
      { label: '成功分组', value: success, sub: pct(success, total), color: '#67c23a' },
      { label: '兜底 0000', value: fallback, sub: pct(fallback, total), color: '#e6a23c' },
      { label: '异常失败', value: failed, sub: pct(failed, total), color: '#f56c6c' },
    ]
    fallbackRate.value = total ? Math.round(fallback / total * 100) : 0

    topDrgs.value = drgDist.map((r: any) => ({
      drg_code: r.code, drg_name: r.name, count: r.count,
    }))
    mdcDist.value = mdcDistRes.map((r: any) => ({
      mdc_code: r.code, mdc_name: r.name, count: r.count,
    }))

    recent.value = recentList

    // 兜底明细：按当前日期/科室筛兜底病案
    if (filters.drg_code === '0000') {
      fallbacks.value = recentList.filter(r => r.drg_code === '0000')
    } else if (filters.drg_code) {
      fallbacks.value = []
    } else {
      const fbParams: any = { drg_code: '0000', limit: 20, ...base }
      fallbacks.value = await api.listResults(fbParams).catch(() => [])
    }
  } finally {
    loading.value = false
  }
}

async function loadMeta() {
  // 部门列表 + 所有 DRG 编码：两个并行请求，砍掉原来的 N+1（565 次 HTTP → 2 次）
  const [depts, drgs] = await Promise.all([
    api.listDepartments().catch(() => [] as string[]),
    api.listAllDrgCodes().catch(() => [] as { code: string; name: string | null }[]),
  ])
  departments.value = depts
  allDrgCodes.value = drgs
}

function pct(n: number, total: number) {
  return total > 0 ? Math.round(n / total * 100) : 0
}

function ccTag(level: string) {
  return level === 'MCC' ? 'danger' : level === 'CC' ? 'warning' : 'info'
}

function goDetail(row: any) {
  router.push(`/result/${row.result_id}`)
}

function openDrgCases(row: any) {
  drillDialog.title = `DRG ${row.drg_code}${row.drg_name ? ' · ' + row.drg_name : ''}  ·  共 ${row.count} 条`
  drillDialog.filter = { drg_code: row.drg_code }
  drillDialog.visible = true
  loadDrill()
}

function openMdcCases(row: any) {
  drillDialog.title = `MDC ${row.mdc_code}${row.mdc_name ? ' · ' + row.mdc_name : ''}  ·  共 ${row.count} 条`
  drillDialog.filter = { mdc_code: row.mdc_code }
  drillDialog.visible = true
  loadDrill()
}

async function loadDrill() {
  drillDialog.loading = true
  try {
    const params: any = { ...drillDialog.filter, limit: 500 }
    // 复用当前顶栏筛选条件（下钻保持语境一致）
    if (dateRange.value?.[0]) params.discharge_date_from = dateRange.value[0]
    if (dateRange.value?.[1]) params.discharge_date_to = dateRange.value[1]
    if (filters.discharge_department) params.discharge_department = filters.discharge_department
    drillDialog.cases = await api.listResults(params)
  } finally {
    drillDialog.loading = false
  }
}

function goDetailFromDrill(row: any) {
  drillDialog.visible = false
  router.push(`/result/${row.result_id}`)
}

function clearFilters() {
  dateRange.value = null
  filters.discharge_department = undefined
  filters.drg_code = undefined
  load()
}

onMounted(async () => {
  await loadMeta()
  await load()
})
</script>

<style scoped>
.stat-card { text-align: center; padding: 16px 0; }
.stat-value { font-size: 36px; font-weight: bold; }
.stat-label { color: #666; margin-top: 8px; }
.stat-sub { color: #999; font-size: 12px; }
.fallback-chart { text-align: center; padding: 24px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
</style>