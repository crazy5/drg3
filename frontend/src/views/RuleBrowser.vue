<template>
  <el-container style="height: calc(100vh - 120px)">
    <el-aside width="280px" style="background: #fff; border-right: 1px solid #eee; overflow-y: auto">
      <div class="pane-header">MDC（{{ mdcs.length }}）</div>
      <div
        v-for="m in mdcs"
        :key="m.code"
        class="list-item"
        :class="{ active: selectedMdc === m.code }"
        @click="selectMdc(m.code)"
      >
        <span class="code">{{ m.code }}</span>
        <span class="name">{{ m.name }}</span>
      </div>
    </el-aside>

    <el-aside width="320px" style="background: #fff; border-right: 1px solid #eee; overflow-y: auto">
      <div class="pane-header">ADRG（{{ adrgs.length }}）</div>
      <div v-if="!selectedMdc" class="hint">← 请先选择 MDC</div>
      <div
        v-for="a in adrgs"
        :key="a.code"
        class="list-item"
        :class="{ active: selectedAdrg === a.code }"
        @click="selectAdrg(a.code)"
      >
        <span class="code">{{ a.code }}</span>
        <span class="name">{{ a.name }}</span>
        <el-tag size="small" :type="typeColor(a.adrg_type)" style="margin-left: auto">
          {{ typeLabel(a.adrg_type) }}
        </el-tag>
      </div>
    </el-aside>

    <el-main style="background: #fff; overflow-y: auto">
      <div v-if="!selectedAdrg" class="hint-center">请从左侧选择规则查看</div>

      <template v-else>
        <h2>ADRG: {{ selectedAdrg }} — {{ adrgDetail?.adrg_name }}</h2>
        <RuleSyntax :expr="adrgDetail?.rule_expr || ''" />

        <el-divider />

        <h3>DRG 子规则（{{ drgs.length }}）</h3>
        <el-collapse v-model="openDrg">
          <el-collapse-item
            v-for="d in drgs"
            :key="d.code"
            :name="d.code"
          >
            <template #title>
              <span class="drg-title">
                <el-tag type="success" size="small">{{ d.code }}</el-tag>
                <span style="margin-left: 8px">{{ d.name }}</span>
                <code v-if="d.rule_expr" class="drg-expr">{{ d.rule_expr }}</code>
              </span>
            </template>
            <RuleSyntax v-if="d.rule_expr" :expr="d.rule_expr" />
            <div v-else class="hint">（继承 ADRG 规则，无独立表达式）</div>
          </el-collapse-item>
        </el-collapse>
      </template>
    </el-main>

    <el-aside width="320px" style="background: #fff; border-left: 1px solid #eee; overflow-y: auto">
      <div class="pane-header">集合浏览（{{ filteredSets.length }} / {{ sets.length }}）</div>
      <el-input v-model="setQuery" placeholder="搜索集合（如 OP_GG1 / DI_G00）..." clearable style="margin-bottom: 12px" />
      <div
        v-for="s in filteredSets"
        :key="s.set_id"
        class="list-item set-item"
        @click="openSet(s.set_id)"
      >
        <span class="code">{{ s.set_id }}</span>
        <span class="name">{{ s.size }} 项</span>
      </div>
    </el-aside>
  </el-container>

  <SetInspector v-if="setDrawer" v-model="setDrawer" :set-id="currentSet" />
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '@/api'
import RuleSyntax from '@/components/RuleSyntax.vue'
import SetInspector from '@/components/SetInspector.vue'

const mdcs = ref<any[]>([])
const adrgs = ref<any[]>([])
const adrgDetail = ref<any>(null)
const drgs = ref<any[]>([])
const sets = ref<any[]>([])
const setQuery = ref('')

const selectedMdc = ref<string>('')
const selectedAdrg = ref<string>('')
const openDrg = ref<string[]>([])

const setDrawer = ref(false)
const currentSet = ref<string>('')

const filteredSets = computed(() => {
  const q = setQuery.value.trim().toUpperCase()
  if (!q) return sets.value
  return sets.value.filter(s => s.set_id.toUpperCase().includes(q))
})

async function load() {
  mdcs.value = await api.listMdc()
  sets.value = await api.listSets()
}

async function selectMdc(code: string) {
  selectedMdc.value = code
  selectedAdrg.value = ''
  adrgDetail.value = null
  drgs.value = []
  adrgs.value = await api.listAdrg(code)
}

async function selectAdrg(code: string) {
  selectedAdrg.value = code
  adrgDetail.value = adrgs.value.find(a => a.code === code)
  drgs.value = await api.listDrg(code)
  openDrg.value = drgs.value.slice(0, 3).map(d => d.code)
}

function openSet(id: string) {
  currentSet.value = id
  setDrawer.value = true
}

function typeColor(t: string) {
  if (t === 'surgical') return 'danger'
  if (t === 'medical') return ''
  if (t === 'composite') return 'warning'
  return 'info'
}

function typeLabel(t: string) {
  return { surgical: '手术', medical: '内科', composite: '复合', fallback: '兜底' }[t] || t
}

onMounted(load)
</script>

<style scoped>
.pane-header { padding: 12px 16px; background: #fafafa; font-weight: bold; border-bottom: 1px solid #eee; position: sticky; top: 0; }
.list-item { padding: 10px 16px; border-bottom: 1px solid #f5f5f5; cursor: pointer; display: flex; align-items: center; gap: 8px; }
.list-item:hover { background: #f5f7fa; }
.list-item.active { background: #ecf5ff; border-left: 3px solid #409eff; }
.code { font-weight: bold; color: #409eff; min-width: 70px; font-family: monospace; }
.name { color: #666; font-size: 13px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hint { color: #999; padding: 16px; font-size: 13px; }
.hint-center { color: #999; text-align: center; padding: 80px 0; font-size: 14px; }
.drg-title { display: flex; align-items: center; gap: 8px; width: 100%; }
.drg-expr { color: #666; font-size: 12px; margin-left: auto; max-width: 400px; overflow: hidden; text-overflow: ellipsis; }
.set-item { font-size: 13px; }
</style>