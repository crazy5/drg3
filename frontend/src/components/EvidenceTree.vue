<template>
  <div class="evidence-tree">
    <h3>证据链（命中路径）</h3>
    <el-tag v-for="p in evidence.matched_path || []" :key="p" type="success" style="margin-right: 8px">
      {{ p }}
    </el-tag>

    <h3 style="margin-top: 24px">各阶段尝试记录</h3>
    <el-collapse v-for="stage in evidence.stages || []" :key="stage.stage">
      <el-collapse-item :title="stageTitle(stage)" :name="stage.stage">
        <div v-for="rule in stage.tried_rules" :key="rule.rule_code" class="rule-row">
          <div class="rule-head">
            <el-tag :type="rule.matched ? 'success' : 'info'" size="small">
              {{ rule.matched ? '✓ 命中' : '✗ 未命中' }}
            </el-tag>
            <span class="rule-code">{{ rule.rule_code }}</span>
            <code v-if="rule.raw_expr" class="rule-expr">{{ rule.raw_expr }}</code>
            <el-tag v-if="rule.skip_reason" type="warning" size="small">{{ rule.skip_reason }}</el-tag>
          </div>
          <div v-if="rule.children && rule.children.length" class="children">
            <div v-for="(child, i) in rule.children" :key="i" class="child-node">
              <template v-if="child.node === 'InCheck'">
                <span class="child-label">in 判定:</span>
                <span class="child-text">{{ child.variables?.join(' / ') }} {{ child.negated ? 'not in' : 'in' }} {{ child.target_sets?.join(' / ') }}</span>
                <el-tag size="small" :type="child.matched ? 'success' : 'danger'">
                  {{ child.matched ? '命中' : '未命中' }}
                </el-tag>
                <span v-if="child.hit_codes?.length" class="hit-codes">
                  <span v-for="(hc, i) in child.hit_codes" :key="hc">
                    → {{ hc }}<span v-if="nameOf(hc)" style="color: #303133; font-weight: normal; margin-right: 4px"> ({{ nameOf(hc) }})</span><span v-if="i < child.hit_codes.length - 1">; </span>
                  </span>
                </span>
              </template>
              <template v-else-if="child.node === 'Compare'">
                <span class="child-label">比较:</span>
                <span class="child-text">{{ child.variable }} {{ child.op }} {{ child.expected }} (实际 {{ child.actual }})</span>
                <el-tag size="small" :type="child.result ? 'success' : 'danger'">
                  {{ child.result ? '✓' : '✗' }}
                </el-tag>
              </template>
              <template v-else-if="child.label === 'CCPrecheck'">
                <span class="child-label">CC 预判:</span>
                <span class="child-text">
                  主诊断 <code>{{ child.main_diagnosis }}</code>
                  <span v-if="nameOf(child.main_diagnosis)" style="margin-left: 4px; color: #67c23a">{{ nameOf(child.main_diagnosis) }}</span>
                  命中排除表: {{ (child.main_exclusion_tables || []).join(', ') || '无' }}
                </span>
              </template>
              <template v-else-if="child.label === 'MCC'">
                <span class="child-label">MCC 判定:</span>
                <span v-if="child.hit" class="hit-codes">{{ child.reason }}</span>
                <span v-else-if="child.skipped" class="skipped">跳过: {{ child.reason }}</span>
                <span v-else>未匹配任何 MCC</span>
              </template>
              <template v-else-if="child.label === 'CC'">
                <span class="child-label">CC 判定:</span>
                <span v-if="child.hit" class="hit-codes">{{ child.reason }}</span>
                <span v-else-if="child.skipped" class="skipped">跳过: {{ child.reason }}</span>
                <span v-else>未匹配任何 CC</span>
              </template>
              <template v-else-if="child.label?.startsWith('and.') || child.label?.startsWith('or.')">
                <span class="child-label">{{ child.label }}:</span>
                <el-tag v-if="child.skipped_short_circuit" type="info" size="small">短路跳过</el-tag>
                <el-tag v-else-if="child.not_evaluated" type="info" size="small">未求值</el-tag>
                <span v-else class="child-text">result = {{ child.result }}</span>
                <div v-if="child.children" class="nested">
                  <div v-for="(c2, j) in child.children" :key="j" class="child-node">
                    <span class="child-label">{{ c2.label }}:</span>
                    <span class="child-text">
                      <template v-if="c2.node === 'InCheck'">
                        {{ c2.variables?.join(' / ') }} {{ c2.negated ? 'not in' : 'in' }} {{ c2.target_sets?.join(' / ') }}
                        <el-tag size="small" :type="c2.matched ? 'success' : 'danger'">
                          {{ c2.matched ? '命中' : '未命中' }}
                        </el-tag>
                        <span v-if="c2.hit_codes?.length">
                          <span v-for="(hc, i) in c2.hit_codes" :key="hc">
                            → {{ hc }}<span v-if="nameOf(hc)" style="color: #303133; font-weight: normal"> ({{ nameOf(hc) }})</span><span v-if="i < c2.hit_codes.length - 1">; </span>
                          </span>
                        </span>
                      </template>
                      <template v-else-if="c2.node === 'Compare'">
                        {{ c2.variable }} {{ c2.op }} {{ c2.expected }} = {{ c2.result }}
                      </template>
                      <template v-else>{{ JSON.stringify(c2).slice(0, 80) }}</template>
                    </span>
                  </div>
                </div>
              </template>
              <template v-else>
                <span class="child-label">{{ child.label || child.node }}:</span>
                <span class="child-text">{{ JSON.stringify(child).slice(0, 80) }}</span>
              </template>
            </div>
          </div>
        </div>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from '@/api'

const props = defineProps<{ evidence: any }>()
const codeNames = ref<Record<string, string>>({})

watch(
  () => props.evidence,
  async (ev) => {
    const all = new Set<string>()
    for (const stage of ev?.stages || []) {
      for (const rule of stage.tried_rules || []) {
        for (const c of rule.children || []) {
          if (c.hit_codes) for (const code of c.hit_codes) all.add(code)
          if (c.diagnosis) all.add(c.diagnosis)
        }
      }
    }
    if (all.size === 0) { codeNames.value = {}; return }
    try {
      codeNames.value = await api.getNames([...all])
    } catch {
      codeNames.value = {}
    }
  },
  { immediate: true },
)

function nameOf(code: string): string {
  return codeNames.value[code] || ''
}

function stageTitle(s: any) {
  const matched = s.matched_rule || '未命中'
  return `${s.stage} → ${matched}`
}
</script>

<style scoped>
.evidence-tree { background: #fff; padding: 16px; border-radius: 8px; margin-top: 16px; }
.rule-row { padding: 8px 0; border-bottom: 1px dashed #eee; }
.rule-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.rule-code { font-weight: bold; color: #1890ff; }
.rule-expr { background: #f0f0f0; padding: 2px 8px; border-radius: 4px; font-size: 12px; }
.children { margin-top: 8px; padding-left: 24px; }
.child-node { padding: 4px 0; font-size: 13px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.child-label { color: #888; min-width: 80px; }
.child-text { color: #333; }
.hit-codes { color: #67c23a; font-weight: bold; }
.skipped { color: #e6a23c; }
.nested { margin-left: 24px; border-left: 2px solid #eee; padding-left: 12px; margin-top: 4px; }
</style>
