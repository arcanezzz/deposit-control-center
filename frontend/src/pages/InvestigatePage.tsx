// Investigation screen: every deposit, filterable; click a row for the full event timeline + retry.
// Filters live in the URL (?employee=..&status=..), so they survive refresh and the back button.
import { Alert, Button, Card, Col, Collapse, DatePicker, Row, Select, Space, Statistic, Table, Tag, Tooltip, Typography } from 'antd'
import { ClearOutlined, ReloadOutlined } from '@ant-design/icons'
import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { AccountsPanel } from '../components/AccountsPanel'
import { DepositDrawer } from '../components/DepositDrawer'
import { StatusBadge, STATUS_LABEL } from '../components/StatusBadge'
import { useEmployee } from '../context/EmployeeContext'
import { dayjs, formatDateTime, formatMoney, shortId } from '../format'
import { usePolling } from '../hooks/usePolling'
import { describeError } from '../messages'
import type { Account, Deposit, DepositPage, DepositStatus } from '../types'

const { Text } = Typography
const STATUSES: DepositStatus[] = ['PENDING', 'PROCESSING', 'COMPLETED', 'FAILED']
const STATUS_COLOR: Record<DepositStatus, string> = { PENDING: '#8c8c8c', PROCESSING: '#1677ff', COMPLETED: '#52c41a', FAILED: '#ff4d4f' }

export function InvestigatePage() {
  const { employees } = useEmployee()
  const [params, setParams] = useSearchParams()
  const [accounts, setAccounts] = useState<Account[]>([])
  const [page, setPage] = useState<DepositPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)

  const employee = params.get('employee') ?? undefined
  const account = params.get('account') ?? undefined
  const statuses = (params.get('status') ?? '').split(',').filter(Boolean) as DepositStatus[]
  const from = params.get('from') ?? undefined
  const to = params.get('to') ?? undefined
  const pageNo = Number(params.get('page') ?? '1')
  const pageSize = Number(params.get('size') ?? '20')
  const openId = params.get('deposit')

  const update = (changes: Record<string, string | undefined | null>, resetPage = true) => {
    const next = new URLSearchParams(params)
    Object.entries(changes).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)))
    if (resetPage) next.delete('page')
    setParams(next, { replace: true })
  }

  const query = useMemo(() => {
    const q = new URLSearchParams()
    if (employee) q.set('employee_id', employee)
    if (account) q.set('account_id', account)
    statuses.forEach((s) => q.append('status', s))
    if (from) q.set('created_from', from)
    if (to) q.set('created_to', to)
    q.set('limit', String(pageSize))
    q.set('offset', String((pageNo - 1) * pageSize))
    return q
  }, [employee, account, statuses.join(','), from, to, pageNo, pageSize])

  usePolling(
    async () => {
      try {
        setPage(await api.searchDeposits(query))
        setError(null)
        setUpdatedAt(new Date())
      } catch (e) {
        setError(describeError(e).title)
      }
    },
    3000,
    true,
    [query.toString()],
  )
  usePolling(async () => setAccounts(await api.accounts()), 10000)

  const empName = (id: string) => employees.find((e) => e.id === id)?.name ?? id
  const accLabel = (id: string) => {
    const a = accounts.find((x) => x.id === id)
    return a ? `${a.account_number} · ${a.owner_name}` : id
  }

  const activeFilters = [
    employee && { key: 'employee', label: `עובד: ${empName(employee)}` },
    account && { key: 'account', label: `חשבון: ${accLabel(account)}` },
    statuses.length > 0 && { key: 'status', label: `סטטוס: ${statuses.map((s) => STATUS_LABEL[s]).join(', ')}` },
    (from || to) && { key: 'dates', label: `תאריכים: ${from ?? '…'} – ${to ?? '…'}` },
  ].filter(Boolean) as { key: string; label: string }[]

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      {/* summary tiles (information, not filters) */}
      <Row gutter={[16, 16]}>
        {STATUSES.map((s) => (
          <Col xs={12} md={6} key={s}>
            <Card size="small">
              <Statistic title={STATUS_LABEL[s]} value={page?.status_counts?.[s] ?? 0} valueStyle={{ color: STATUS_COLOR[s] }} />
            </Card>
          </Col>
        ))}
      </Row>

      <Card size="small" title="סינון">
        <Space wrap size="middle" style={{ width: '100%' }}>
          <Select allowClear placeholder="עובד" style={{ minWidth: 180 }} value={employee} onChange={(v) => update({ employee: v })}
            options={employees.map((e) => ({ value: e.id, label: `${e.name} (${e.id})` }))} />
          <Select allowClear showSearch optionFilterProp="label" placeholder="חשבון" style={{ minWidth: 220 }} value={account} onChange={(v) => update({ account: v })}
            options={accounts.map((a) => ({ value: a.id, label: `${a.account_number} · ${a.owner_name}` }))} />
          <Select mode="multiple" allowClear placeholder="סטטוס" style={{ minWidth: 220 }} value={statuses}
            onChange={(v: string[]) => update({ status: v.join(',') })}
            options={STATUSES.map((s) => ({ value: s, label: STATUS_LABEL[s] }))} />
          <DatePicker.RangePicker
            placeholder={['מתאריך יצירה', 'עד תאריך']}
            allowEmpty={[true, true]}
            value={[from ? dayjs(from) : null, to ? dayjs(to) : null]}
            onChange={(v) => update({ from: v?.[0]?.format('YYYY-MM-DD'), to: v?.[1]?.format('YYYY-MM-DD') })}
            format="DD/MM/YYYY"
          />
          <Button icon={<ClearOutlined />} disabled={activeFilters.length === 0}
            onClick={() => update({ employee: null, account: null, status: null, from: null, to: null })}>נקה הכל</Button>
        </Space>
        {activeFilters.length > 0 && (
          <div style={{ marginTop: 12 }}>
            {activeFilters.map((f) => (
              <Tag key={f.key} closable color="blue" onClose={() => update(f.key === 'dates' ? { from: null, to: null } : { [f.key]: null })}>
                {f.label}
              </Tag>
            ))}
          </div>
        )}
      </Card>

      {error && <Alert type="error" showIcon message={error} />}

      <Card
        size="small"
        title={`בקשות הפקדה (${page?.total ?? 0})`}
        extra={<Space><ReloadOutlined spin={false} /><Text type="secondary" style={{ fontSize: 12 }}>מתעדכן כל 3 שניות · עודכן ב־{updatedAt?.toLocaleTimeString('he-IL') ?? '—'}</Text></Space>}
      >
        <Table<Deposit>
          rowKey="id"
          size="middle"
          dataSource={page?.items ?? []}
          loading={!page && !error}
          scroll={{ x: 1200 }}
          onRow={(r) => ({ onClick: () => update({ deposit: r.id }, false), style: { cursor: 'pointer' } })}
          rowClassName={(r) => (r.id === openId ? 'ant-table-row-selected' : '')}
          pagination={{
            current: pageNo,
            pageSize,
            total: page?.total ?? 0,
            showSizeChanger: true,
            pageSizeOptions: [20, 50, 100],
            onChange: (p, s) => update({ page: String(p), size: String(s) }, false),
          }}
          columns={[
            { title: 'מזהה', dataIndex: 'id', width: 110, render: (id: string) => <Text code copyable={{ text: id }}>{shortId(id)}</Text> },
            { title: 'עובד יוזם', render: (_, r) => r.employee_name },
            { title: 'חשבון', render: (_, r) => <>{r.account_number}<br /><Text type="secondary" style={{ fontSize: 12 }}>{r.owner_name}</Text></> },
            { title: 'סכום', align: 'left', render: (_, r) => <span dir="ltr" style={{ fontFamily: 'monospace' }}>{formatMoney(r.amount)}</span> },
            { title: 'סטטוס', render: (_, r) => <StatusBadge deposit={r} /> },
            { title: 'נוצר', render: (_, r) => <span style={{ fontSize: 12 }}>{formatDateTime(r.created_at)}</span> },
            { title: 'עודכן', render: (_, r) => <span style={{ fontSize: 12 }}>{formatDateTime(r.updated_at)}</span> },
            { title: 'ניסיונות', dataIndex: 'attempts', align: 'center', width: 80 },
            {
              title: 'סיבת כשל',
              ellipsis: { showTitle: false },
              render: (_, r) =>
                r.failure_reason ? (
                  <Tooltip title={`${r.failure_reason} (${r.failure_code})`}>
                    <Text type={r.status !== 'FAILED' ? 'secondary' : r.failure_retryable ? 'warning' : 'danger'}>
                      {r.status !== 'FAILED' && 'אחרון: '}{r.failure_reason}
                    </Text>
                  </Tooltip>
                ) : <Text type="secondary">—</Text>,
            },
          ]}
        />
      </Card>

      <Collapse items={[{ key: 'acc', label: 'חשבונות ויתרות (כולל חסימה לצורך הדגמה)', children: <AccountsPanel /> }]} />

      <DepositDrawer depositId={openId} onClose={() => update({ deposit: null }, false)} />
    </Space>
  )
}
