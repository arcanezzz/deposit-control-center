import dayjs from 'dayjs'
import utc from 'dayjs/plugin/utc'
import timezone from 'dayjs/plugin/timezone'
import relativeTime from 'dayjs/plugin/relativeTime'
import 'dayjs/locale/he'

dayjs.extend(utc)
dayjs.extend(timezone)
dayjs.extend(relativeTime)
dayjs.locale('he')

export const TZ = 'Asia/Jerusalem'

const ils = new Intl.NumberFormat('he-IL', { style: 'currency', currency: 'ILS', minimumFractionDigits: 2 })

// Money arrives as an exact decimal string; Number() here is for DISPLAY formatting only.
export const formatMoney = (value: string | null | undefined) => (value == null ? '—' : ils.format(Number(value)))

export const formatDateTime = (iso: string | null | undefined) =>
  iso ? dayjs(iso).tz(TZ).format('DD/MM/YYYY HH:mm:ss') : '—'

export const formatTimeMs = (iso: string) => dayjs(iso).tz(TZ).format('HH:mm:ss.SSS')

export const fromNow = (iso: string | null | undefined) => (iso ? dayjs(iso).fromNow() : '—')

export function formatElapsed(fromIso: string, toIso: string): string {
  const ms = dayjs(toIso).diff(dayjs(fromIso))
  if (ms < 1000) return `+${ms}ms`
  if (ms < 60_000) return `+${(ms / 1000).toFixed(1)}s`
  const m = Math.floor(ms / 60_000)
  return `+${m}m ${Math.round((ms % 60_000) / 1000)}s`
}

export const shortId = (id: string) => id.slice(0, 8)

// Same rule as the API: positive, up to 2 decimals, no exponent.
export const AMOUNT_RE = /^\d{1,13}(\.\d{1,2})?$/

export { dayjs }
