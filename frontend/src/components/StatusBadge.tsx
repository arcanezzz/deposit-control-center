// Status is always text + icon + color (never color alone).
import { Tag, Tooltip } from 'antd'
import { CheckCircleOutlined, ClockCircleOutlined, CloseCircleOutlined, SyncOutlined, WarningOutlined } from '@ant-design/icons'
import type { Deposit } from '../types'

export const STATUS_LABEL: Record<string, string> = {
  PENDING: 'ממתין',
  PROCESSING: 'בעיבוד',
  COMPLETED: 'הושלם',
  FAILED: 'נכשל',
}

export function StatusBadge({ deposit }: { deposit: Pick<Deposit, 'status' | 'failure_retryable'> }) {
  switch (deposit.status) {
    case 'PENDING':
      return <Tooltip title="הבקשה נשמרה וממתינה לעיבוד. לא בוצע זיכוי."><Tag icon={<ClockCircleOutlined />} color="default">ממתין</Tag></Tooltip>
    case 'PROCESSING':
      return <Tooltip title="ניסיון עיבוד התחיל. טרם נשמר זיכוי."><Tag icon={<SyncOutlined spin />} color="processing">בעיבוד</Tag></Tooltip>
    case 'COMPLETED':
      return <Tooltip title="הזיכוי והתוצאה נשמרו. החשבון זוכה פעם אחת."><Tag icon={<CheckCircleOutlined />} color="success">הושלם</Tag></Tooltip>
    case 'FAILED':
      return deposit.failure_retryable ? (
        <Tooltip title="כשל זמני — ללא זיכוי. ניתן לנסות שוב."><Tag icon={<WarningOutlined />} color="warning">כשל זמני</Tag></Tooltip>
      ) : (
        <Tooltip title="כשל סופי — ללא זיכוי. לא ניתן לנסות שוב."><Tag icon={<CloseCircleOutlined />} color="error">כשל סופי</Tag></Tooltip>
      )
  }
}
