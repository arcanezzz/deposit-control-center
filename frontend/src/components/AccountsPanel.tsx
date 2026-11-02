// Demo helper: account balances + block/unblock (to demonstrate a permanent failure:
// block an account while a delayed deposit to it is in PROCESSING).
import { Button, Table, Tag, Typography, message } from 'antd'
import { useState } from 'react'
import { api } from '../api'
import { formatMoney } from '../format'
import { usePolling } from '../hooks/usePolling'
import { describeError } from '../messages'
import type { Account } from '../types'

export function AccountsPanel() {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  usePolling(async () => setAccounts(await api.accounts()), 3000)

  const toggle = async (a: Account) => {
    setBusy(a.id)
    try {
      await api.setAccountStatus(a.id, a.status === 'ACTIVE' ? 'BLOCKED' : 'ACTIVE')
      setAccounts(await api.accounts())
    } catch (e) {
      message.error(describeError(e).title)
    } finally {
      setBusy(null)
    }
  }

  return (
    <>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        יתרה = יתרת פתיחה + סכום הזיכויים. חסימת חשבון בזמן שהפקדה אליו מעוכבת בעיבוד תגרום לכשל סופי (ללא זיכוי).
      </Typography.Paragraph>
      <Table<Account>
        size="small"
        rowKey="id"
        pagination={false}
        dataSource={accounts}
        scroll={{ x: true }}
        columns={[
          { title: 'חשבון', render: (_, a) => <>{a.account_number}<br /><Typography.Text type="secondary" style={{ fontSize: 12 }}>{a.id}</Typography.Text></> },
          { title: 'בעלים', dataIndex: 'owner_name' },
          { title: 'יתרה', align: 'left', render: (_, a) => <span dir="ltr" style={{ fontFamily: 'monospace' }}>{formatMoney(a.balance)}</span> },
          { title: 'מצב', render: (_, a) => (a.status === 'ACTIVE' ? <Tag color="green">פעיל</Tag> : <Tag color="red">חסום</Tag>) },
          {
            title: '',
            render: (_, a) => (
              <Button size="small" danger={a.status === 'ACTIVE'} loading={busy === a.id} onClick={() => toggle(a)}>
                {a.status === 'ACTIVE' ? 'חסום' : 'שחרר'}
              </Button>
            ),
          },
        ]}
      />
    </>
  )
}
