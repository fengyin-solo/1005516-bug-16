<template>
  <section class="page" data-module="transformer">
    <header class="page-head">
      <div>
        <h2>变压器监视管理</h2>
        <p class="page-desc">维护变压器，围绕变压器编号、电压等级、额定容量、油温上限做登记、筛选与状态流转。</p>
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
      <label class="filter-item">
        <span>运行状态</span>
        <select v-model="filters.status">
          <option value="">全部</option>
          <option v-for="item in statuses" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>判定判语</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="row in rows" :key="String(row.id)">
          <tr>
            <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
            <td>{{ row['判语'] || '—' }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="toggleEditor(row)">判定/改上限</button>
              <button class="link" type="button" @click="runAction('恢复正常', row)">恢复正常</button>
            </td>
          </tr>
          <tr v-if="editorId === row.id" class="editor-row">
            <td :colspan="columns.length + 2">
              <form class="judge-form" @submit.prevent="submitJudgment(row)">
                <label>
                  <span>油温上限(℃)</span>
                  <input v-model.number="draft.油温上限" type="number" min="1" />
                </label>
                <label>
                  <span>油温(℃)</span>
                  <input v-model.number="draft.油温" type="number" />
                </label>
                <label>
                  <span>绕组温度(℃)</span>
                  <input v-model.number="draft.绕组温度" type="number" />
                </label>
                <label>
                  <span>油位状态</span>
                  <select v-model="draft.油位状态">
                    <option value="正常">正常</option>
                    <option value="偏低">偏低</option>
                  </select>
                </label>
                <label>
                  <span>瓦斯保护状态</span>
                  <select v-model="draft.瓦斯保护状态">
                    <option value="正常">正常</option>
                    <option value="动作">动作</option>
                  </select>
                </label>
                <button class="btn primary" type="submit">提交判定</button>
                <button class="btn" type="button" @click="saveSettings(row)">只保存油温上限</button>
                <button class="btn ghost" type="button" @click="editorId = null">取消</button>
              </form>
            </td>
          </tr>
        </template>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无变压器监视数据，可先登记变压器</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条变压器监视记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/transformer'
const columns = ["变压器编号", "电压等级", "额定容量", "油温上限", "油温", "绕组温度", "油位状态", "瓦斯保护状态", "运行状态"]
const statuses = ["正常运行", "油温偏高", "高温报警"]
const stats = [{"label": "运行变压器", "value": 0}, {"label": "超温台数", "value": 0}, {"label": "待检台数", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const editorId = ref<number | null>(null)
const draft = reactive<Record<string, string | number>>({
  油温上限: 85,
  油温: '',
  绕组温度: '',
  油位状态: '正常',
  瓦斯保护状态: '正常',
})

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  // 与列表同一套过滤条件，避免导出条数和列表对不上。
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  window.open(`${ENDPOINT}/export${query ? `?${query}` : ''}`, '_blank')
}

function openCreate() {
  errorMessage.value = '变压器登记入口尚未接入审批流'
}

function toggleEditor(row: Row) {
  if (editorId.value === row.id) {
    editorId.value = null
    return
  }
  editorId.value = row.id as number
  draft.油温上限 = (row['油温上限'] as number) ?? 85
  draft.油温 = (row['油温'] as number) ?? ''
  draft.绕组温度 = (row['绕组温度'] as number) ?? ''
  draft.油位状态 = (row['油位状态'] as string) || '正常'
  draft.瓦斯保护状态 = (row['瓦斯保护状态'] as string) || '正常'
  errorMessage.value = ''
}

function payloadFor(row: Row, extra: Record<string, unknown>) {
  return {
    values: {
      油温上限: draft.油温上限,
      油温: draft.油温,
      绕组温度: draft.绕组温度,
      油位状态: draft.油位状态,
      瓦斯保护状态: draft.瓦斯保护状态,
      ...extra,
    },
  }
}

async function submitJudgment(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/judgment`, {
      method: 'POST',
      body: JSON.stringify(payloadFor(row, {})),
    })
    const result = await response.json()
    if (!response.ok || result.ok === false) {
      throw new Error(result.message || '判定未生效，请稍后重试')
    }
    editorId.value = null
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '判定提交失败'
  }
}

async function saveSettings(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`, {
      method: 'PUT',
      body: JSON.stringify({ values: { 油温上限: draft.油温上限 } }),
    })
    const result = await response.json()
    if (!response.ok || result.ok === false) {
      throw new Error(result.message || '油温上限保存失败')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '参数保存失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const result = await response.json()
    if (!response.ok || result.ok === false) {
      throw new Error(result?.message || '变压器监视动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '变压器监视操作失败'
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
.editor-row td {
  background: #f8fafc;
  padding: 10px 12px;
}
.judge-form {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
}
.judge-form label span {
  display: block;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 2px;
}
.judge-form input,
.judge-form select {
  padding: 5px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  min-width: 96px;
}
</style>
