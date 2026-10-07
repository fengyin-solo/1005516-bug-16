<template>
  <section class="page" data-module="transformer">
    <header class="page-head">
      <div>
        <h2>变压器监视管理</h2>
        <p class="page-desc">围绕油温上限、绕组温度、油位状态、瓦斯保护状态做统一判定：正常运行 → 油温偏高 → 高温报警逐档流转，油位异常自动同步检修台账。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记变压器</button>
        <button class="btn" type="button" @click="exportRows">导出变压器监视清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>判定结论</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row['判定结论'] ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openJudge(row)">判定录入</button>
            <button class="link" type="button" @click="openEditLimit(row)">改油温上限</button>
            <button
              v-if="row.status !== '正常运行'"
              class="link"
              type="button"
              @click="runAction('恢复正常', row)"
            >
              恢复正常
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无变压器监视数据，可先登记变压器</td>
        </tr>
      </tbody>
    </table>

    <!-- 判定录入 -->
    <div v-if="judgeOpen" class="judge-panel">
      <h3>判定录入 · {{ judgeForm.id }}（{{ judgeForm['变压器编号'] }}）</h3>
      <p class="page-desc">口径：绕组温度 ≥ 油温上限为高温报警；达到上限 90% 为油温偏高；油位偏低为油位异常；冲突取更重档。状态只逐档向上，相同读数重复提交只生效一次。</p>
      <div class="judge-grid">
        <label><span>油温上限(℃)</span>
          <input v-model="judgeForm['油温上限']" type="number" />
        </label>
        <label><span>绕组温度(℃)</span>
          <input v-model="judgeForm['绕组温度']" type="number" />
        </label>
        <label><span>油位状态</span>
          <select v-model="judgeForm['油位状态']">
            <option>正常</option>
            <option>偏低</option>
          </select>
        </label>
        <label><span>瓦斯保护状态</span>
          <select v-model="judgeForm['瓦斯保护状态']">
            <option>正常</option>
            <option>动作</option>
          </select>
        </label>
      </div>
      <div class="judge-foot">
        <button class="btn primary" type="button" :disabled="submitting" @click="submitJudge">提交判定</button>
        <button class="btn ghost" type="button" @click="judgeOpen = false">取消</button>
      </div>
    </div>

    <!-- 修改油温上限 -->
    <div v-if="editOpen" class="judge-panel">
      <h3>修改油温上限 · {{ editForm.id }}（{{ editForm['变压器编号'] }}）</h3>
      <div class="judge-grid">
        <label><span>油温上限(℃)</span>
          <input v-model="editForm['油温上限']" type="number" />
        </label>
      </div>
      <div class="judge-foot">
        <button class="btn primary" type="button" :disabled="submitting" @click="submitEdit">保存</button>
        <button class="btn ghost" type="button" @click="editOpen = false">取消</button>
      </div>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条变压器监视记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/transformer'
const columns = ["变压器编号", "电压等级", "额定容量", "油温上限", "绕组温度", "油位状态", "瓦斯保护状态", "运行状态"]
const statuses = ["正常运行", "油温偏高", "高温报警"]
const stats = [{"label": "运行变压器", "value": 0}, {"label": "超温台数", "value": 0}, {"label": "待检台数", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const submitting = ref(false)

const judgeOpen = ref(false)
const judgeForm = ref<Row>({})
const editOpen = ref(false)
const editForm = ref<Row>({})

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '变压器登记入口尚未接入审批流'
}

function openJudge(row: Row) {
  errorMessage.value = ''
  judgeForm.value = {
    id: row.id,
    '变压器编号': row['变压器编号'],
    '油温上限': row['油温上限'] ?? '',
    '绕组温度': row['绕组温度'] ?? '',
    '油位状态': (row['油位状态'] === '偏低' ? '偏低' : '正常'),
    '瓦斯保护状态': (row['瓦斯保护状态'] === '动作' ? '动作' : '正常'),
  }
  editOpen.value = false
  judgeOpen.value = true
}

function openEditLimit(row: Row) {
  errorMessage.value = ''
  editForm.value = { id: row.id, '变压器编号': row['变压器编号'], '油温上限': row['油温上限'] ?? '' }
  judgeOpen.value = false
  editOpen.value = true
}

async function submitJudge() {
  const form = judgeForm.value
  const values = {
    '油温上限': form['油温上限'],
    '绕组温度': form['绕组温度'],
    '油位状态': form['油位状态'],
    '瓦斯保护状态': form['瓦斯保护状态'],
  }
  await postAction(`${ENDPOINT}/${form.id}/judgement`, values, '判定未生效')
  judgeOpen.value = false
}

async function submitEdit() {
  const form = editForm.value
  await postAction(`${ENDPOINT}/${form.id}`, { '油温上限': form['油温上限'] }, 'PUT')
  editOpen.value = false
}

async function postAction(url: string, values: Record<string, unknown>, method: string) {
  errorMessage.value = ''
  submitting.value = true
  try {
    const response = await request(url, {
      method,
      body: JSON.stringify({ values }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message ?? '操作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '变压器操作失败'
  } finally {
    submitting.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message ?? '动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '变压器操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('变压器列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '变压器监视列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.judge-panel {
  margin: 16px 0;
  padding: 16px;
  border: 1px solid #d8dee8;
  border-radius: 10px;
  background: #fafbfd;
}
.judge-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin: 12px 0;
}
.judge-grid label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: #5a6472;
}
.judge-grid input,
.judge-grid select {
  padding: 7px 10px;
  border: 1px solid #cfd6e0;
  border-radius: 6px;
}
.judge-foot {
  display: flex;
  gap: 10px;
}
</style>
