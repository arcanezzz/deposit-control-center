// Thin fetch wrapper. Distinguishes three outcomes, which matter for a money UI:
//   - success (2xx)
//   - ApiError: the server answered with {error: {code, message, details}}
//   - NetworkError: no answer (timeout / connection lost) -> the outcome is UNKNOWN,
//     so the UI offers "resend with the same Idempotency-Key".
import type { Account, DepositAccepted, DepositDetail, DepositPage, DepositPayload, Employee } from './types'

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details: unknown) {
    super(message)
  }
}

export class NetworkError extends Error {}

async function request<T>(path: string, init: RequestInit = {}, timeoutMs = 10000): Promise<{ data: T; status: number }> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  let res: Response
  try {
    res = await fetch(path, {
      ...init,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...(init.headers || {}) },
    })
  } catch (e) {
    throw new NetworkError(e instanceof Error ? e.message : 'network error')
  } finally {
    clearTimeout(timer)
  }
  let body: any = null
  try {
    body = await res.json()
  } catch {
    /* empty or non-JSON body (e.g. nginx 502) */
  }
  if (!res.ok) {
    if (body?.error) throw new ApiError(res.status, body.error.code, body.error.message, body.error.details)
    if (res.status >= 502) throw new NetworkError(`gateway error ${res.status}`)
    throw new ApiError(res.status, 'HTTP_ERROR', `HTTP ${res.status}`, null)
  }
  return { data: body as T, status: res.status }
}

export function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  // Fallback for non-secure contexts (randomUUID needs https or localhost)
  const b = crypto.getRandomValues(new Uint8Array(16))
  b[6] = (b[6] & 0x0f) | 0x40
  b[8] = (b[8] & 0x3f) | 0x80
  const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

export const api = {
  employees: () => request<Employee[]>('/api/employees').then((r) => r.data),
  accounts: () => request<Account[]>('/api/accounts').then((r) => r.data),
  setAccountStatus: (id: string, status: 'ACTIVE' | 'BLOCKED') =>
    request<Account>(`/api/accounts/${id}/status`, { method: 'PATCH', body: JSON.stringify({ status }) }).then((r) => r.data),
  createDeposit: (payload: DepositPayload, idempotencyKey: string) =>
    request<DepositAccepted>('/api/deposits', {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: { 'Idempotency-Key': idempotencyKey },
    }),
  getDeposit: (id: string) => request<DepositDetail>(`/api/deposits/${id}`).then((r) => r.data),
  searchDeposits: (params: URLSearchParams) => request<DepositPage>(`/api/deposits?${params}`).then((r) => r.data),
  retryDeposit: (id: string, employeeId: string) =>
    request<DepositAccepted>(`/api/deposits/${id}/retry`, {
      method: 'POST',
      body: JSON.stringify({ employee_id: employeeId }),
    }).then((r) => r.data),
}
