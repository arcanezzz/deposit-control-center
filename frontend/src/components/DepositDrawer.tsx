import { Alert, Button, Descriptions, Drawer, Space, Spin, Tooltip, Typography } from 'antd'
import { RedoOutlined } from '@ant-design/icons'
import { useState } from 'react'
import { api } from '../api'
import { useEmployee } from '../context/EmployeeContext'
import { formatDateTime, formatMoney } from '../format'
import { usePolling } from '../hooks/usePolling'
import { describeError } from '../messages'
import type { DepositDetail } from '../types'
import { DepositTimeline } from './DepositTimeline'
import { StatusBadge } from './StatusBadge'

const { Text, Title } = Typography

function retryBlockedReason(d: DepositDetail, hasEmployee: boolean): string | null {
  if (!hasEmployee) return 'יש לבחור עובד בראש המסך'
  if (d.status === 'COMPLETED') return 'ההפקדה הושלמה — ניסיון חוזר אסור'
  if (d.status === 'PENDING' || d.status === 'PROCESSING') return 'בקשת עיבוד כבר פעילה'
  if (d.status === 'FAILED' && !d.failure_retryable) return 'כשל סופי — לא ניתן לנסות שוב'
  return null
}

export function DepositDrawer({ depositId, onClose }: { depositId: string | null; onClose: () => void }) {
  const { employee } = useEmployee()
  const [deposit, setDeposit] = useState<DepositDetail | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [retryResult, setRetryResult] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [retrying, setRetrying] = useState(false)

  usePolling(
    async () => {
      if (!depositId) return
      try {
        const d = await api.getDeposit(depositId)
        setDeposit(d)
        setLoadError(null)
      } catch (e) {
        setLoadError(describeError(e).title)
      }
    },
    2000,
    !!depositId,
    [depositId],
  )

  const close = () => {
    setDeposit(null)
    setRetryResult(null)
    onClose()
  }

  const retry = async () => {
    if (!deposit || !employee) return
    setRetrying(true)
    setRetryResult(null)
    try {
      await api.retryDeposit(deposit.id, employee.id)
      setRetryResult({ type: 'success', text: `ניסיון חוזר התקבל לעיבוד (בוצע ע"י ${employee.name}). ההפקדה חזרה ל"ממתין".` })
      setDeposit(await api.getDeposit(deposit.id))
    } catch (e) {
      const err = describeError(e)
      setRetryResult({ type: 'error', text: err.title })
    } finally {
      setRetrying(false)
    }
  }

  const d = deposit && deposit.id === depositId ? deposit : null
  const blocked = d ? retryBlockedReason(d, !!employee) : null

  return (
    <Drawer open={!!depositId} onClose={close} width={620} placement="left" title="פרטי הפקדה ותחקור" destroyOnClose>
      {loadError && <Alert type="error" showIcon message={loadError} />}
      {!d && !loadError && <Spin />}
      {d && (
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          <div>
            <Title level={2} style={{ margin: 0 }}><span dir="ltr">{formatMoney(d.amount)}</span></Title>
            <Space>
              <StatusBadge deposit={d} />
              <Text type="secondary">ניסיונות עיבוד: {d.attempts}</Text>
            </Space>
          </div>

          {d.failure_reason && (
            <Alert
              type={d.status === 'FAILED' ? (d.failure_retryable ? 'warning' : 'error') : 'info'}
              showIcon
              message={d.status === 'FAILED' ? 'סיבת הכשל' : 'סיבת הכשל האחרון'}
              description={<>{d.failure_reason} <Text type="secondary">({d.failure_code})</Text></>}
            />
          )}

          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="מזהה הפקדה"><Text copyable code>{d.id}</Text></Descriptions.Item>
            <Descriptions.Item label="עובד יוזם">{d.employee_name} ({d.employee_id})</Descriptions.Item>
            <Descriptions.Item label="חשבון">{d.account_number} · {d.owner_name} ({d.account_id})</Descriptions.Item>
            <Descriptions.Item label="נוצר">{formatDateTime(d.created_at)}</Descriptions.Item>
            <Descriptions.Item label="עודכן">{formatDateTime(d.updated_at)}</Descriptions.Item>
            <Descriptions.Item label="הושלם">{formatDateTime(d.completed_at)}</Descriptions.Item>
            <Descriptions.Item label="זיכוי">
              {d.credit ? <>זוכה {formatMoney(d.credit.amount)} · יתרה לאחר: {formatMoney(d.credit.balance_after)}</> : <Text type="secondary">לא בוצע זיכוי</Text>}
            </Descriptions.Item>
            <Descriptions.Item label="Idempotency-Key"><Text copyable code style={{ fontSize: 12 }}>{d.idempotency_key}</Text></Descriptions.Item>
            {(d.simulate_delay_seconds > 0 || d.simulate_transient_failure) && (
              <Descriptions.Item label="הדגמה">
                {d.simulate_delay_seconds > 0 && `עיכוב ${d.simulate_delay_seconds} ש׳ `}
                {d.simulate_transient_failure && '· כשל זמני בניסיון הראשון'}
              </Descriptions.Item>
            )}
          </Descriptions>

          <div>
            <Tooltip title={blocked}>
              <Button type="primary" icon={<RedoOutlined />} disabled={!!blocked} loading={retrying} onClick={retry}>
                נסה שוב{employee ? ` (כ־${employee.name})` : ''}
              </Button>
            </Tooltip>
            {retryResult && <Alert style={{ marginTop: 12 }} type={retryResult.type} showIcon message={retryResult.text} />}
          </div>

          <div>
            <Title level={5}>ציר אירועים</Title>
            <DepositTimeline events={d.events} />
          </div>
        </Space>
      )}
    </Drawer>
  )
}
