// Hebrew texts for API error codes and event types. The API returns stable codes;
// the UI owns the wording.
import { ApiError, NetworkError } from './api'

export const ERROR_TEXT: Record<string, string> = {
  INVALID_AMOUNT: 'סכום לא תקין',
  AMOUNT_TOO_LARGE: 'הסכום חורג מהמגבלה להפקדה בודדת (1,000,000 ₪)',
  ACCOUNT_BLOCKED: 'החשבון חסום — לא ניתן להפקיד אליו',
  ACCOUNT_NOT_FOUND: 'החשבון אינו קיים',
  EMPLOYEE_NOT_FOUND: 'העובד אינו קיים',
  IDEMPOTENCY_KEY_REQUIRED: 'חסר מזהה מניעת כפילות (Idempotency-Key)',
  INVALID_IDEMPOTENCY_KEY: 'מזהה מניעת כפילות לא תקין',
  IDEMPOTENCY_KEY_REUSED: 'מזהה מניעת הכפילות כבר שימש להפקדה עם נתונים אחרים (עובד / חשבון / סכום). לפעולה חדשה לחץ "בקשה חדשה".',
  DEPOSIT_NOT_FOUND: 'ההפקדה לא נמצאה',
  DEPOSIT_ALREADY_COMPLETED: 'ההפקדה כבר הושלמה וזוכתה — ניסיון חוזר אסור',
  PROCESSING_ALREADY_ACTIVE: 'בקשת עיבוד להפקדה זו כבר פעילה (ממתינה או בעיבוד)',
  PERMANENT_FAILURE: 'ההפקדה נכשלה בכשל סופי — לא ניתן לנסות שוב',
  INVALID_DATE_RANGE: 'טווח תאריכים לא תקין: תאריך ההתחלה אחרי תאריך הסיום',
  INVALID_STATUS: 'סטטוס לא מוכר',
  VALIDATION_ERROR: 'הבקשה אינה תקינה',
  DATABASE_UNAVAILABLE: 'מסד הנתונים אינו זמין כרגע — נסה שוב',
  INTERNAL_ERROR: 'שגיאת שרת לא צפויה',
}

const AMOUNT_REASON: Record<string, string> = {
  required: 'יש להזין סכום',
  negative: 'הסכום חייב להיות חיובי',
  not_positive: 'הסכום חייב להיות גדול מאפס',
  too_many_decimals: 'עד שתי ספרות אחרי הנקודה',
  invalid_format: 'יש להזין מספר עשרוני רגיל, למשל 1250.50',
}

export function describeError(e: unknown): { title: string; description?: string; code?: string } {
  if (e instanceof NetworkError) {
    return { title: 'אין תשובה מהשרת', description: 'לא ידוע אם הבקשה התקבלה.' }
  }
  if (e instanceof ApiError) {
    let title = ERROR_TEXT[e.code] ?? e.message
    const d = e.details as any
    if (e.code === 'INVALID_AMOUNT' && d?.reason && AMOUNT_REASON[d.reason]) title = `סכום לא תקין: ${AMOUNT_REASON[d.reason]}`
    return { title, description: e.message, code: e.code }
  }
  return { title: 'שגיאה לא צפויה', description: String(e) }
}

export const EVENT_TEXT: Record<string, string> = {
  RECEIVED: 'הבקשה התקבלה ונשמרה',
  IDEMPOTENT_REPLAY: 'שליחה חוזרת עם אותו מזהה — הוחזרה אותה הפקדה, ללא זיכוי נוסף',
  PUBLISHED: 'נשלחה לעיבוד ב־Kafka',
  REPUBLISHED: 'נשלחה מחדש ל־Kafka (watchdog)',
  ATTEMPT_STARTED: 'התחלת ניסיון עיבוד',
  FAILED: 'הניסיון נכשל — לא בוצע זיכוי',
  RETRY_REQUESTED: 'התבקש ניסיון חוזר',
  COMPLETED: 'הזיכוי הושלם',
  RECOVERED_INTERRUPTED: 'שחזור: עיבוד שנקטע זוהה',
  DUPLICATE_IGNORED: 'הודעה כפולה התקבלה — התעלמות, ללא זיכוי',
  STALE_MESSAGE_IGNORED: 'הודעה ישנה (לפני ניסיון חוזר) — התעלמות',
  CREDIT_ABORTED: 'הזיכוי בוטל — מצב ההפקדה השתנה במהלך הניסיון',
}
