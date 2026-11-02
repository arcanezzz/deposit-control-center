-- Synthetic demo data. Idempotent: every INSERT uses fixed ids + ON CONFLICT DO NOTHING,
-- so re-running never resets balances and never duplicates rows.
-- Invariant kept by the seed: accounts.balance = opening_balance + SUM(credits.amount).
-- request_hash format (must match the API): sha256("<employee_id>|<account_id>|<amount with 2 decimals>")

INSERT INTO employees (id, name, role) VALUES
    ('E-1001', 'דנה כהן',      'טלרית'),
    ('E-1002', 'יוסי לוי',     'מנהל סניף'),
    ('E-1003', 'מיכל אברהם',   'בנקאית'),
    ('E-1004', 'אחמד חורי',    'טלר'),
    ('E-1005', 'נועה פרידמן',  'בק־אופיס')
ON CONFLICT (id) DO NOTHING;

INSERT INTO accounts (id, account_number, owner_name, opening_balance, balance, status) VALUES
    ('ACC-001', '12-601-100001', 'אורי שמש',        10000.00, 10500.00, 'ACTIVE'),
    ('ACC-002', '12-601-100002', 'רונית בן דוד',     2500.00,  3700.50, 'ACTIVE'),
    ('ACC-003', '12-602-200003', 'חברת אלפא בע"מ',  150000.00, 150000.00, 'ACTIVE'),
    ('ACC-004', '12-602-200004', 'סאמר נסאר',           0.00,      0.00, 'ACTIVE'),
    ('ACC-005', '12-603-300005', 'תמר גולן',          820.75,    820.75, 'ACTIVE'),
    ('ACC-006', '12-603-300006', 'משה ישראלי',       4300.00,   4300.00, 'BLOCKED')
ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Historical deposits so the investigation screen has content on first run.
-- ---------------------------------------------------------------------------
INSERT INTO deposits (id, idempotency_key, request_hash, employee_id, account_id, amount, status,
                      failure_code, failure_reason, failure_retryable, attempts, current_dispatch_id,
                      simulate_delay_seconds, simulate_transient_failure,
                      processing_started_at, created_at, updated_at, completed_at) VALUES
    -- 1. Plain success
    ('11111111-1111-4111-8111-000000000001', 'seed-0001',
     encode(sha256(convert_to('E-1001|ACC-001|500.00', 'UTF8')), 'hex'),
     'E-1001', 'ACC-001', 500.00, 'COMPLETED', NULL, NULL, NULL, 1,
     'aaaaaaaa-0000-4000-8000-000000000001', 0, false,
     '2026-10-28 09:15:01+02', '2026-10-28 09:15:00+02', '2026-10-28 09:15:02+02', '2026-10-28 09:15:02+02'),
    -- 2. Transient failure, retried by a different employee, then success
    ('11111111-1111-4111-8111-000000000002', 'seed-0002',
     encode(sha256(convert_to('E-1003|ACC-002|1200.50', 'UTF8')), 'hex'),
     'E-1003', 'ACC-002', 1200.50, 'COMPLETED', NULL, NULL, NULL, 2,
     'aaaaaaaa-0000-4000-8000-000000000003', 0, true,
     '2026-10-29 14:05:11+02', '2026-10-29 14:00:00+02', '2026-10-29 14:05:12+02', '2026-10-29 14:05:12+02'),
    -- 3. Transient failure still waiting for a retry (use it to demo retry)
    ('11111111-1111-4111-8111-000000000003', 'seed-0003',
     encode(sha256(convert_to('E-1004|ACC-003|250.00', 'UTF8')), 'hex'),
     'E-1004', 'ACC-003', 250.00, 'FAILED', 'SIMULATED_TRANSIENT',
     'כשל זמני מדומה לפני הזיכוי (ניסיון ראשון)', true, 1,
     'aaaaaaaa-0000-4000-8000-000000000004', 0, true,
     '2026-10-30 11:20:01+02', '2026-10-30 11:20:00+02', '2026-10-30 11:20:02+02', NULL),
    -- 4. Permanent failure: account was blocked after the request was accepted
    ('11111111-1111-4111-8111-000000000004', 'seed-0004',
     encode(sha256(convert_to('E-1005|ACC-006|300.00', 'UTF8')), 'hex'),
     'E-1005', 'ACC-006', 300.00, 'FAILED', 'ACCOUNT_BLOCKED',
     'החשבון חסום — לא ניתן לזכות. כשל סופי, אין לנסות שוב', false, 1,
     'aaaaaaaa-0000-4000-8000-000000000005', 0, false,
     '2026-10-31 16:40:01+02', '2026-10-31 16:40:00+02', '2026-10-31 16:40:02+02', NULL)
ON CONFLICT (id) DO NOTHING;

INSERT INTO credits (id, deposit_id, account_id, amount, balance_after, created_at) VALUES
    ('22222222-2222-4222-8222-000000000001', '11111111-1111-4111-8111-000000000001', 'ACC-001',  500.00, 10500.00, '2026-10-28 09:15:02+02'),
    ('22222222-2222-4222-8222-000000000002', '11111111-1111-4111-8111-000000000002', 'ACC-002', 1200.50,  3700.50, '2026-10-29 14:05:12+02')
ON CONFLICT (id) DO NOTHING;

INSERT INTO deposit_events (id, deposit_id, event_type, actor_employee_id, attempt_no, details, created_at) VALUES
    -- deposit 1
    ('33333333-3333-4333-8333-000000000101', '11111111-1111-4111-8111-000000000001', 'RECEIVED',        'E-1001', NULL, '{"amount": "500.00", "account_id": "ACC-001"}', '2026-10-28 09:15:00.000+02'),
    ('33333333-3333-4333-8333-000000000102', '11111111-1111-4111-8111-000000000001', 'PUBLISHED',       NULL,     NULL, '{"topic": "deposit-requests", "dispatch_id": "aaaaaaaa-0000-4000-8000-000000000001"}', '2026-10-28 09:15:00.100+02'),
    ('33333333-3333-4333-8333-000000000103', '11111111-1111-4111-8111-000000000001', 'ATTEMPT_STARTED', NULL,     1,    '{"dispatch_id": "aaaaaaaa-0000-4000-8000-000000000001"}', '2026-10-28 09:15:01+02'),
    ('33333333-3333-4333-8333-000000000104', '11111111-1111-4111-8111-000000000001', 'COMPLETED',       NULL,     1,    '{"balance_after": "10500.00"}', '2026-10-28 09:15:02+02'),
    -- deposit 2
    ('33333333-3333-4333-8333-000000000201', '11111111-1111-4111-8111-000000000002', 'RECEIVED',        'E-1003', NULL, '{"amount": "1200.50", "account_id": "ACC-002"}', '2026-10-29 14:00:00.000+02'),
    ('33333333-3333-4333-8333-000000000202', '11111111-1111-4111-8111-000000000002', 'PUBLISHED',       NULL,     NULL, '{"topic": "deposit-requests", "dispatch_id": "aaaaaaaa-0000-4000-8000-000000000002"}', '2026-10-29 14:00:00.100+02'),
    ('33333333-3333-4333-8333-000000000203', '11111111-1111-4111-8111-000000000002', 'ATTEMPT_STARTED', NULL,     1,    '{"dispatch_id": "aaaaaaaa-0000-4000-8000-000000000002"}', '2026-10-29 14:00:01+02'),
    ('33333333-3333-4333-8333-000000000204', '11111111-1111-4111-8111-000000000002', 'FAILED',          NULL,     1,    '{"failure_code": "SIMULATED_TRANSIENT", "retryable": true, "reason": "כשל זמני מדומה לפני הזיכוי (ניסיון ראשון)"}', '2026-10-29 14:00:02+02'),
    ('33333333-3333-4333-8333-000000000205', '11111111-1111-4111-8111-000000000002', 'RETRY_REQUESTED', 'E-1002', NULL, '{"dispatch_id": "aaaaaaaa-0000-4000-8000-000000000003"}', '2026-10-29 14:05:10+02'),
    ('33333333-3333-4333-8333-000000000206', '11111111-1111-4111-8111-000000000002', 'PUBLISHED',       NULL,     NULL, '{"topic": "deposit-requests", "dispatch_id": "aaaaaaaa-0000-4000-8000-000000000003"}', '2026-10-29 14:05:10.100+02'),
    ('33333333-3333-4333-8333-000000000207', '11111111-1111-4111-8111-000000000002', 'ATTEMPT_STARTED', NULL,     2,    '{"dispatch_id": "aaaaaaaa-0000-4000-8000-000000000003"}', '2026-10-29 14:05:11+02'),
    ('33333333-3333-4333-8333-000000000208', '11111111-1111-4111-8111-000000000002', 'COMPLETED',       NULL,     2,    '{"balance_after": "3700.50"}', '2026-10-29 14:05:12+02'),
    -- deposit 3
    ('33333333-3333-4333-8333-000000000301', '11111111-1111-4111-8111-000000000003', 'RECEIVED',        'E-1004', NULL, '{"amount": "250.00", "account_id": "ACC-003"}', '2026-10-30 11:20:00.000+02'),
    ('33333333-3333-4333-8333-000000000302', '11111111-1111-4111-8111-000000000003', 'PUBLISHED',       NULL,     NULL, '{"topic": "deposit-requests", "dispatch_id": "aaaaaaaa-0000-4000-8000-000000000004"}', '2026-10-30 11:20:00.100+02'),
    ('33333333-3333-4333-8333-000000000303', '11111111-1111-4111-8111-000000000003', 'ATTEMPT_STARTED', NULL,     1,    '{"dispatch_id": "aaaaaaaa-0000-4000-8000-000000000004"}', '2026-10-30 11:20:01+02'),
    ('33333333-3333-4333-8333-000000000304', '11111111-1111-4111-8111-000000000003', 'FAILED',          NULL,     1,    '{"failure_code": "SIMULATED_TRANSIENT", "retryable": true, "reason": "כשל זמני מדומה לפני הזיכוי (ניסיון ראשון)"}', '2026-10-30 11:20:02+02'),
    -- deposit 4
    ('33333333-3333-4333-8333-000000000401', '11111111-1111-4111-8111-000000000004', 'RECEIVED',        'E-1005', NULL, '{"amount": "300.00", "account_id": "ACC-006"}', '2026-10-31 16:40:00.000+02'),
    ('33333333-3333-4333-8333-000000000402', '11111111-1111-4111-8111-000000000004', 'PUBLISHED',       NULL,     NULL, '{"topic": "deposit-requests", "dispatch_id": "aaaaaaaa-0000-4000-8000-000000000005"}', '2026-10-31 16:40:00.100+02'),
    ('33333333-3333-4333-8333-000000000403', '11111111-1111-4111-8111-000000000004', 'ATTEMPT_STARTED', NULL,     1,    '{"dispatch_id": "aaaaaaaa-0000-4000-8000-000000000005"}', '2026-10-31 16:40:01+02'),
    ('33333333-3333-4333-8333-000000000404', '11111111-1111-4111-8111-000000000004', 'FAILED',          NULL,     1,    '{"failure_code": "ACCOUNT_BLOCKED", "retryable": false, "reason": "החשבון חסום — לא ניתן לזכות. כשל סופי, אין לנסות שוב"}', '2026-10-31 16:40:02+02')
ON CONFLICT (id) DO NOTHING;
