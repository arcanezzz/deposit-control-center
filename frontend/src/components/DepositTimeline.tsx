import { Timeline, Typography } from 'antd'
import {
  CheckCircleFilled, CloseCircleFilled, CopyOutlined, ExclamationCircleFilled, InboxOutlined, PlayCircleOutlined,
  RedoOutlined, SendOutlined, StopOutlined, ToolOutlined,
} from '@ant-design/icons'
import type { ReactNode } from 'react'
import { EVENT_TEXT } from '../messages'
import { formatElapsed, formatMoney, formatTimeMs, formatDateTime } from '../format'
import type { DepositEvent } from '../types'

const { Text } = Typography

function visual(e: DepositEvent): { color: string; dot: ReactNode } {
  switch (e.event_type) {
    case 'RECEIVED': return { color: 'blue', dot: <InboxOutlined /> }
    case 'PUBLISHED':
    case 'REPUBLISHED': return { color: 'gray', dot: <SendOutlined /> }
    case 'ATTEMPT_STARTED': return { color: 'blue', dot: <PlayCircleOutlined /> }
    case 'COMPLETED': return { color: 'green', dot: <CheckCircleFilled /> }
    case 'FAILED':
      return e.details.retryable
        ? { color: 'orange', dot: <ExclamationCircleFilled /> }
        : { color: 'red', dot: <CloseCircleFilled /> }
    case 'RETRY_REQUESTED': return { color: 'purple', dot: <RedoOutlined /> }
    case 'RECOVERED_INTERRUPTED': return { color: 'orange', dot: <ToolOutlined /> }
    case 'IDEMPOTENT_REPLAY': return { color: 'gray', dot: <CopyOutlined /> }
    default: return { color: 'gray', dot: <StopOutlined /> }
  }
}

function detailLine(e: DepositEvent): ReactNode {
  const d = e.details as Record<string, any>
  switch (e.event_type) {
    case 'RECEIVED': return `סכום ${formatMoney(d.amount)} לחשבון ${d.account_id}`
    case 'PUBLISHED':
    case 'REPUBLISHED':
      return [`topic ${d.topic}`, d.partition != null && `partition ${d.partition}`, d.offset != null && `offset ${d.offset}`]
        .filter(Boolean)
        .join(' · ')
    case 'ATTEMPT_STARTED': return `worker ${d.worker ?? ''}`
    case 'FAILED': return <><Text type={d.retryable ? 'warning' : 'danger'}>{d.reason}</Text> <Text type="secondary">({d.failure_code})</Text></>
    case 'COMPLETED': return `יתרה לאחר זיכוי: ${formatMoney(d.balance_after)}${d.note ? ` · ${d.note}` : ''}`
    case 'RETRY_REQUESTED': return `כשל קודם: ${d.previous_failure_code ?? '—'}`
    case 'RECOVERED_INTERRUPTED':
      return d.action === 'marked_failed_retryable'
        ? `העיבוד לא הסתיים תוך ${d.lease_seconds} שניות (התחיל ${formatDateTime(d.processing_started_at)}). לא בוצע זיכוי.`
        : 'נמצא זיכוי קיים — הסטטוס תוקן ל"הושלם"'
    case 'DUPLICATE_IGNORED': return `סטטוס בזמן ההודעה: ${d.status}`
    case 'STALE_MESSAGE_IGNORED': return `dispatch ${String(d.message_dispatch_id).slice(0, 8)} הוחלף`
    case 'CREDIT_ABORTED': return d.reason
    default: return null
  }
}

export function DepositTimeline({ events }: { events: DepositEvent[] }) {
  return (
    <Timeline
      items={events.map((e, i) => {
        const v = visual(e)
        const actor = e.actor_name ? `${e.actor_name} (${e.actor_employee_id})` : 'מערכת'
        return {
          key: e.id,
          color: v.color,
          dot: v.dot,
          children: (
            <div>
              <div>
                <Text strong>{EVENT_TEXT[e.event_type] ?? e.event_type}</Text>
                {e.attempt_no ? <Text type="secondary"> · ניסיון #{e.attempt_no}</Text> : null}
              </div>
              <div style={{ fontSize: 12 }}>
                <Text type="secondary" style={{ direction: 'ltr', display: 'inline-block' }}>{formatTimeMs(e.created_at)}</Text>
                {i > 0 && <Text type="secondary"> · <span dir="ltr">{formatElapsed(events[i - 1].created_at, e.created_at)}</span></Text>}
                <Text type="secondary"> · {actor}</Text>
              </div>
              <div style={{ fontSize: 12 }}>{detailLine(e)}</div>
            </div>
          ),
        }
      })}
    />
  )
}
