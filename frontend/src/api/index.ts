import client from './client'

export interface CodeOut {
  code: string
  name?: string | null
}

export interface CaseSummary {
  zyzd: CodeOut
  zyss?: CodeOut | null
  qtzd_list: CodeOut[]
  qtss_list: CodeOut[]
  nl?: number
  xb?: number
  xsrtl?: number
  xsrtz?: number
  admission_date?: string | null
  discharge_date?: string | null
  discharge_department?: string | null
}

export interface CaseIn {
  case_id?: string
  zyzd: string
  zyss?: string
  qtzd_list?: string[]
  qtss_list?: string[]
  nl?: number
  xb?: number
  xsrtl?: number
  xsrtz?: number
  admission_date?: string           // 'YYYY-MM-DD'
  discharge_date?: string
  discharge_department?: string
}

export interface GroupResult {
  mdc_code: string | null
  mdc_name?: string | null
  adrg_code: string | null
  adrg_name?: string | null
  drg_code: string
  drg_name?: string | null
  cc_level: 'MCC' | 'CC' | 'NONE'
  error_type: string
  error_msg?: string
  evidence: any
  case_summary?: CaseSummary
  duration_ms: number
  case_id?: string
  result_id?: number
}

export const api = {
  // 单条病案 + 分组
  createCase: (c: CaseIn) => client.post<any, GroupResult>('/cases', c),

  // 批量
  batchGroup: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return client.post<any, { job_id: string; total: number; status: string }>('/grouping/batch', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  getJob: (jobId: string) => client.get<any, any>(`/grouping/jobs/${jobId}`),

  // 结果
  getResult: (id: number) => client.get<any, GroupResult>(`/results/${id}`),
  listResults: (params?: {
    drg_code?: string
    mdc_code?: string
    adrg_code?: string
    discharge_date_from?: string
    discharge_date_to?: string
    discharge_department?: string
    limit?: number
    offset?: number
  }) => client.get<any, GroupResult[]>('/results', { params }),

  // 统计聚合（SQL GROUP BY / COUNT，精确）
  statsCount: (params?: {
    drg_code?: string
    mdc_code?: string
    adrg_code?: string
    discharge_date_from?: string
    discharge_date_to?: string
    discharge_department?: string
  }) => client.get<any, { total: number }>('/results/stats/count', { params }),
  statsErrorRate: (params?: {
    discharge_date_from?: string
    discharge_date_to?: string
    discharge_department?: string
  }) => client.get<any, { success: number; fallback: number; error: number; total: number }>(
    '/results/stats/error-rate', { params },
  ),
  statsDistribution: (params?: {
    by: 'drg' | 'mdc'
    discharge_date_from?: string
    discharge_date_to?: string
    discharge_department?: string
  }) => client.get<any, { code: string; name: string | null; count: number }[]>(
    '/results/stats/distribution', { params },
  ),

  // 出院科室下拉
  listDepartments: () => client.get<any, string[]>('/cases/departments'),

  // 规则
  importRules: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return client.post<any, any>('/rules/import', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  listMdc: () => client.get<any, any[]>('/rules/mdc'),
  listAdrg: (mdcCode?: string) => client.get<any, any[]>('/rules/adrg', { params: { mdc_code: mdcCode } }),
  listDrg: (adrgCode?: string) => client.get<any, any[]>('/rules/drg', { params: { adrg_code: adrgCode } }),
  listAllDrgCodes: () => client.get<any, { code: string; name: string | null }[]>('/rules/drg-codes'),
  getDrgAst: (code: string) => client.get<any, any>(`/rules/drg/${code}/ast`),
  listSets: (q?: string) => client.get<any, any[]>('/rules/sets', { params: { q } }),
  getSetMembers: (setId: string) => client.get<any, any[]>(`/rules/sets/${setId}/members`),

  // ICD 中文名
  getName: (code: string) =>
    client.get<any, { code: string; name: string | null }>(`/codes/${encodeURIComponent(code)}/name`),
  getNames: (codes: string[]) =>
    client.get<any, Record<string, string>>('/codes/names', { params: { codes: codes.join(',') } }),
}
