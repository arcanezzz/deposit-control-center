// Execution screen: choose account + amount, send a deposit request, follow its status live.
//
// Idempotency-Key lifecycle (the heart of the double-click / uncertain-network demo):
//   - A key is generated for the current form contents.
//   - Changing ANY field (employee, account, amount, demo options) generates a NEW key.
//   - Clicking "Deposit" again WITHOUT changing anything reuses the key -> the server returns the
//     same deposit (no double deposit by accident).
//   - "Resend same request" re-sends the exact last payload + key.
//   - "New request" generates a new key for a genuinely new deposit with the same fields.
//   - The last request is kept in localStorage, so after a page refresh the status keeps updating.
import {
  Alert, Badge, Button, Card, Col, Collapse, Descriptions, Divider, Form, Input, InputNumber, Row, Select, Space,
  Statistic, Steps, Switch, Tag, Typography,
} from 'antd'
import { CopyOutlined, PlusOutlined, RedoOutlined, SendOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError, NetworkError, newIdempotencyKey } from '../api'
import { StatusBadge } from '../components/StatusBadge'
import { useEmployee } from '../context/EmployeeContext'
import { AMOUNT_RE, formatMoney, fromNow, shortId } from '../format'
import { usePolling } from '../hooks/usePolling'
import { describeError } from '../messages'
import type { Account, DepositAccepted, DepositDetail, DepositPayload } from '../types'

const { Text, Paragraph } = Typography
const LAST_KEY = 'dcc.lastRequest'

interface LastRequest {
  key: string
  payload: DepositPayload
  depositId?: string
  balanceBefore?: string
}

interface SendOutcome {
  kind: 'accepted' | 'replay' | 'error' | 'unknown'
  httpStatus?: number
  depositId?: string
  error?: { title: string; description?: string; code?: string }
  at: string
}

function loadLast(): LastRequest | null {
  try {
    const raw = localStorage.getItem(LAST_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function saveLast(r: LastRequest | null) {
  try {
    if (r) localStorage.setItem(LAST_KEY, JSON.stringify(r))
    else localStorage.removeItem(LAST_KEY)
  } catch {
    /* ignore */
  }
}

function validateAmount(v: string): string | null {
  const t = v.trim()
  if (!t) return 'יש להזין סכום'
  if (t.startsWith('-')) return 'הסכום חייב להיות חיובי'
  if (!AMOUNT_RE.test(t)) return /^\d+\.\d{3,}$/.test(t) ? 'עד שתי ספרות אחרי הנקודה' : 'יש להזין מספר, למשל 1250.50'
  if (Number(t) <= 0) return 'הסכום חייב להיות גדול מאפס'
  if (Number(t) > 1_000_000) return 'מקסימום 1,000,000 ₪ להפקדה בודדת'
  return null
}

export function ExecutePage() {
  const { employee } = useEmployee()
  const [accounts, setAccounts] = useState<Account[]>([])
  const [accountsUpdatedAt, setAccountsUpdatedAt] = useState<string | null>(null)
  const [accountId, setAccountId] = useState<string | undefined>()
  const [amount, setAmount] = useState('')
  const [delay, setDelay] = useState(0)
  const [transient, setTransient] = useState(false)
  const [formKey, setFormKey] = useState(newIdempotencyKey)

  const [last, setLast] = useState<LastRequest | null>(loadLast)
  const [outcomes, setOutcomes] = useState<SendOutcome[]>([])
  const [deposit, setDeposit] = useState<DepositDetail | null>(null)
  const [sending, setSending] = useState(false)

  // Any change to the business data or the demo options = a new logical request = a new key.
  useEffect(() => {
    setFormKey(newIdempotencyKey())
  }, [employee?.id, accountId, amount, delay, transient])

  usePolling(async () => {
    const list = await api.accounts()
    setAccounts(list)
    setAccountsUpdatedAt(new Date().toISOString())
  }, 3000)

  // Poll the tracked deposit every second until it reaches a final state.
  const tracking = !!last?.depositId && !(deposit && deposit.id === last.depositId && ['COMPLETED', 'FAILED'].includes(deposit.status))
  usePolling(
    async () => {
      if (last?.depositId) setDeposit(await api.getDeposit(last.depositId))
    },
    1000,
    tracking,
    [last?.depositId],
  )

  const account = accounts.find((a) => a.id === accountId)
  const amountError = amount ? validateAmount(amount) : null
  const canSend = !!employee && !!accountId && !!amount && !amountError && !sending

  const payload: DepositPayload | null = useMemo(
    () =>
      employee && accountId
        ? { employee_id: employee.id, account_id: accountId, amount: amount.trim(), simulate: { delay_seconds: delay, transient_failure: transient } }
        : null,
    [employee, accountId, amount, delay, transient],
  )

  async function send(p: DepositPayload, key: string): Promise<SendOutcome> {
    const at = new Date().toISOString()
    try {
      const { data, status } = await api.createDeposit(p, key)
      return { kind: (data as DepositAccepted).idempotent_replay ? 'replay' : 'accepted', httpStatus: status, depositId: data.deposit_id, at }
    } catch (e) {
      if (e instanceof NetworkError) return { kind: 'unknown', error: describeError(e), at }
      return { kind: 'error', httpStatus: e instanceof ApiError ? e.status : undefined, error: describeError(e), at }
    }
  }

  function track(p: DepositPayload, key: string, results: SendOutcome[]) {
    const ok = results.find((r) => r.depositId)
    const balanceBefore = accounts.find((a) => a.id === p.account_id)?.balance
    const next: LastRequest = { key, payload: p, depositId: ok?.depositId ?? last?.depositId, balanceBefore }
    if (!ok && last?.key !== key) next.depositId = undefined
    setLast(next)
    saveLast(next)
    setOutcomes(results)
  }

  const onDeposit = async () => {
    if (!payload) return
    setSending(true)
    const r = await send(payload, formKey)
    track(payload, formKey, [r])
    setSending(false)
  }

  const onResend = async () => {
    if (!last) return
    setSending(true)
    const r = await send(last.payload, last.key)
    track(last.payload, last.key, [r])
    setSending(false)
  }

  const onDoubleClick = async () => {
    if (!payload) return
    setSending(true)
    const results = await Promise.all([send(payload, formKey), send(payload, formKey)])
    track(payload, formKey, results)
    setSending(false)
  }

  const onNewRequest = () => {
    setFormKey(newIdempotencyKey())
    setLast(null)
    saveLast(null)
    setOutcomes([])
    setDeposit(null)
  }

  const tracked = deposit && last?.depositId === deposit.id ? deposit : null
  const trackedAccount = accounts.find((a) => a.id === (tracked?.account_id ?? last?.payload.account_id))
  const stepIndex = !tracked ? 0 : tracked.status === 'PENDING' ? 1 : tracked.status === 'PROCESSING' ? 2 : 3
  const stepStatus = tracked?.status === 'FAILED' ? 'error' : tracked?.status === 'COMPLETED' ? 'finish' : 'process'

  return (
    <Row gutter={[24, 24]}>
      {/* ---------------------------------------------------------------- form */}
      <Col xs={24} lg={11}>
        <Card title="ביצוע הפקדה">
          {!employee && <Alert type="warning" showIcon style={{ marginBottom: 16 }} message="יש לבחור עובד בראש המסך לפני ביצוע הפקדה" />}
          <Form layout="vertical" onFinish={onDeposit}>
            <Form.Item label="חשבון" required>
              <Select
                showSearch
                placeholder="בחר חשבון"
                value={accountId}
                onChange={setAccountId}
                optionFilterProp="label"
                options={accounts.map((a) => ({
                  value: a.id,
                  label: `${a.account_number} · ${a.owner_name}`,
                  title: a.id,
                  disabled: false,
                }))}
                optionRender={(o) => {
                  const a = accounts.find((x) => x.id === o.value)!
                  return (
                    <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                      <span>{a.account_number} · {a.owner_name}</span>
                      <span>{a.status === 'BLOCKED' ? <Tag color="red">חסום</Tag> : <Text type="secondary" dir="ltr">{formatMoney(a.balance)}</Text>}</span>
                    </Space>
                  )
                }}
              />
            </Form.Item>

            {account && (
              <Card size="small" style={{ marginBottom: 16, background: 'rgba(22,119,255,0.04)' }}>
                <Space style={{ width: '100%', justifyContent: 'space-between' }} align="start">
                  <Statistic title="יתרה עדכנית" value={formatMoney(account.balance)} valueStyle={{ direction: 'ltr' }} />
                  <Space direction="vertical" align="end" size={0}>
                    {account.status === 'BLOCKED' ? <Tag color="red">חשבון חסום</Tag> : <Tag color="green">פעיל</Tag>}
                    <Text type="secondary" style={{ fontSize: 12 }}>עודכן {fromNow(accountsUpdatedAt)}</Text>
                  </Space>
                </Space>
                {account.status === 'BLOCKED' && <Text type="danger">לא ניתן להפקיד לחשבון חסום — השרת ידחה את הבקשה.</Text>}
              </Card>
            )}

            <Form.Item label="סכום בשקלים" required validateStatus={amountError ? 'error' : undefined} help={amountError ?? 'מספר חיובי, עד שתי ספרות אחרי הנקודה'}>
              <Input
                size="large"
                dir="ltr"
                inputMode="decimal"
                prefix="₪"
                placeholder="0.00"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                allowClear
              />
            </Form.Item>

            <Collapse
              size="small"
              style={{ marginBottom: 16 }}
              items={[{
                key: 'demo',
                label: <Space><ThunderboltOutlined />אפשרויות הדגמה{(delay > 0 || transient) && <Badge status="warning" text="פעיל" />}</Space>,
                children: (
                  <Space direction="vertical" style={{ width: '100%' }}>
                    <Space>
                      <Text>עיכוב עיבוד (שניות):</Text>
                      <InputNumber min={0} max={30} value={delay} onChange={(v) => setDelay(v ?? 0)} />
                    </Space>
                    <Space>
                      <Switch checked={transient} onChange={setTransient} />
                      <Text>כשל זמני בניסיון הראשון (לפני הזיכוי) — יצליח בניסיון הבא</Text>
                    </Space>
                  </Space>
                ),
              }]}
            />

            <Button type="primary" size="large" htmlType="submit" icon={<SendOutlined />} block disabled={!canSend} loading={sending}>
              הפקד
            </Button>
            <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
              מזהה מניעת כפילות לבקשה הנוכחית: <Text code copyable style={{ fontSize: 11 }}>{formKey}</Text>
              <br />שינוי שדה כלשהו יוצר מזהה חדש. לחיצה חוזרת ללא שינוי לא תיצור הפקדה כפולה.
            </Paragraph>
          </Form>
        </Card>
      </Col>

      {/* ---------------------------------------------------------------- last request */}
      <Col xs={24} lg={13}>
        <Card
          title="בקשה אחרונה"
          extra={last && <Button icon={<PlusOutlined />} onClick={onNewRequest}>בקשה חדשה</Button>}
        >
          {!last && outcomes.length === 0 && <Text type="secondary">טרם נשלחה בקשה. מלא את הטופס ולחץ "הפקד".</Text>}

          {outcomes.map((o, i) => (
            <OutcomeAlert key={i} o={o} index={outcomes.length > 1 ? i + 1 : undefined} />
          ))}
          {outcomes.length > 1 && (
            <Alert
              style={{ marginBottom: 12 }}
              type={new Set(outcomes.map((o) => o.depositId)).size === 1 ? 'success' : 'warning'}
              showIcon
              message={new Set(outcomes.map((o) => o.depositId)).size === 1
                ? `שתי הבקשות המקבילות החזירו את אותה הפקדה (${shortId(outcomes[0].depositId ?? '')}) — נוצרה הפקדה אחת בלבד`
                : 'הבקשות החזירו תוצאות שונות'}
            />
          )}

          {tracked && (
            <>
              <Descriptions column={1} size="small" bordered style={{ marginBottom: 16 }}>
                <Descriptions.Item label="מזהה פעולה"><Text copyable code>{tracked.id}</Text></Descriptions.Item>
                <Descriptions.Item label="סטטוס">
                  <Space><StatusBadge deposit={tracked} />{tracking && <Text type="secondary" style={{ fontSize: 12 }}>מתעדכן כל שנייה…</Text>}</Space>
                </Descriptions.Item>
                <Descriptions.Item label="סכום"><span dir="ltr">{formatMoney(tracked.amount)}</span></Descriptions.Item>
                <Descriptions.Item label="חשבון">{tracked.account_number} · {tracked.owner_name}</Descriptions.Item>
                <Descriptions.Item label="ניסיונות עיבוד">{tracked.attempts}</Descriptions.Item>
                {tracked.failure_reason && tracked.status === 'FAILED' && (
                  <Descriptions.Item label="סיבת כשל"><Text type={tracked.failure_retryable ? 'warning' : 'danger'}>{tracked.failure_reason}</Text></Descriptions.Item>
                )}
              </Descriptions>

              <Steps
                size="small"
                current={stepIndex}
                status={stepStatus}
                items={[
                  { title: 'נשלח' },
                  { title: 'התקבל לעיבוד', description: 'ממתין' },
                  { title: 'בעיבוד' },
                  { title: tracked.status === 'FAILED' ? 'נכשל' : 'זוכה' },
                ]}
              />

              {tracked.status === 'COMPLETED' && tracked.credit && (
                <Alert
                  style={{ marginTop: 16 }}
                  type="success"
                  showIcon
                  message="החשבון זוכה"
                  description={<span>יתרה לאחר הזיכוי: <b dir="ltr">{formatMoney(tracked.credit.balance_after)}</b>{last?.balanceBefore && <> (לפני: <span dir="ltr">{formatMoney(last.balanceBefore)}</span>)</>}{trackedAccount && <> · יתרה נוכחית: <span dir="ltr">{formatMoney(trackedAccount.balance)}</span></>}</span>}
                />
              )}
              {tracked.status === 'FAILED' && (
                <Alert
                  style={{ marginTop: 16 }}
                  type={tracked.failure_retryable ? 'warning' : 'error'}
                  showIcon
                  message={tracked.failure_retryable ? 'כשל זמני — החשבון לא זוכה' : 'כשל סופי — החשבון לא זוכה'}
                  description={tracked.failure_retryable ? <>ניתן לבצע ניסיון חוזר ממסך <Link to={`/investigate?deposit=${tracked.id}`}>התחקור</Link>.</> : 'לא ניתן לנסות שוב.'}
                />
              )}
            </>
          )}

          {last && (
            <>
              <Divider orientation="right" plain>הדגמת מניעת כפילות</Divider>
              <Space wrap>
                <Button icon={<RedoOutlined />} onClick={onResend} disabled={sending}>שלח שוב (אותו מזהה)</Button>
                <Button icon={<CopyOutlined />} onClick={onDoubleClick} disabled={!canSend}>לחיצה כפולה (2 בקשות במקביל)</Button>
                {tracked && <Link to={`/investigate?deposit=${tracked.id}`}><Button type="link">פתח בתחקור</Button></Link>}
              </Space>
              <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8 }}>
                "שלח שוב" שולח את אותה בקשה בדיוק עם אותו Idempotency-Key (<Text code style={{ fontSize: 11 }}>{shortId(last.key)}…</Text>) —
                מדמה ניסיון חוזר אחרי חוסר ודאות בתקשורת. השרת יחזיר את אותה הפקדה ללא זיכוי נוסף.
              </Paragraph>
            </>
          )}
        </Card>
      </Col>
    </Row>
  )
}

function OutcomeAlert({ o, index }: { o: SendOutcome; index?: number }) {
  const prefix = index ? `בקשה ${index}: ` : ''
  const time = new Date(o.at).toLocaleTimeString('he-IL')
  if (o.kind === 'accepted')
    return <Alert style={{ marginBottom: 12 }} type="info" showIcon message={`${prefix}הבקשה התקבלה לעיבוד (HTTP ${o.httpStatus})`} description={<>מזהה פעולה: <Text code>{shortId(o.depositId!)}</Text> · החשבון <b>טרם זוכה</b> — הסטטוס יתעדכן אוטומטית. <Text type="secondary">({time})</Text></>} />
  if (o.kind === 'replay')
    return <Alert style={{ marginBottom: 12 }} type="success" showIcon message={`${prefix}שליחה חוזרת זוהתה (HTTP ${o.httpStatus}) — הוחזרה אותה הפקדה`} description={<>מזהה פעולה: <Text code>{shortId(o.depositId!)}</Text> · לא נוצרה הפקדה חדשה ולא יבוצע זיכוי נוסף. <Text type="secondary">({time})</Text></>} />
  if (o.kind === 'unknown')
    return <Alert style={{ marginBottom: 12 }} type="warning" showIcon message={`${prefix}מצב לא ידוע — לא התקבלה תשובה מהשרת`} description={<>ייתכן שהבקשה התקבלה וייתכן שלא. לחץ <b>"שלח שוב (אותו מזהה)"</b> — זה בטוח: אם הבקשה כבר נקלטה תוחזר אותה הפקדה, ללא כפילות. <Text type="secondary">({time})</Text></>} />
  return <Alert style={{ marginBottom: 12 }} type="error" showIcon message={`${prefix}${o.error?.title}`} description={<>{o.error?.code && <Text code>{o.error.code}</Text>} {o.httpStatus && `HTTP ${o.httpStatus}`} <Text type="secondary">({time})</Text></>} />
}
