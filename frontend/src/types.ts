export type DepositStatus = 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED'

export interface Employee { id: string; name: string; role: string }

export interface Account {
  id: string
  account_number: string
  owner_name: string
  balance: string // money is always a string with 2 decimals
  opening_balance: string
  status: 'ACTIVE' | 'BLOCKED'
  updated_at: string
}

export interface Deposit {
  id: string
  idempotency_key: string
  employee_id: string
  employee_name: string
  account_id: string
  account_number: string
  owner_name: string
  amount: string
  status: DepositStatus
  failure_code: string | null
  failure_reason: string | null
  failure_retryable: boolean | null
  attempts: number
  simulate_delay_seconds: number
  simulate_transient_failure: boolean
  processing_started_at: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
}

export interface DepositEvent {
  id: string
  event_type: string
  actor_employee_id: string | null
  actor_name: string | null
  attempt_no: number | null
  details: Record<string, unknown>
  created_at: string
}

export interface Credit {
  id: string
  deposit_id: string
  account_id: string
  amount: string
  balance_after: string
  created_at: string
}

export interface DepositDetail extends Deposit {
  credit: Credit | null
  events: DepositEvent[]
}

export interface DepositAccepted {
  deposit_id: string
  status: DepositStatus
  idempotent_replay: boolean
  message: string
  deposit: Deposit
}

export interface DepositPage {
  items: Deposit[]
  total: number
  limit: number
  offset: number
  status_counts: Record<DepositStatus, number>
}

export interface DepositPayload {
  employee_id: string
  account_id: string
  amount: string
  simulate: { delay_seconds: number; transient_failure: boolean }
}
