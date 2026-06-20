PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  role TEXT NOT NULL CHECK (role IN ('candidate', 'examiner', 'admin')),
  full_name TEXT,
  index_number TEXT UNIQUE,
  username TEXT UNIQUE,
  password_hash TEXT NOT NULL,
  password_salt TEXT NOT NULL,
  active_session_id TEXT,
  approved INTEGER NOT NULL DEFAULT 0,
  approved_at TEXT,
  approved_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  suspended INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('candidate', 'examiner', 'admin')),
  user_agent TEXT,
  created_at TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_audit (
  id TEXT PRIMARY KEY,
  session_id TEXT,
  user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  user_type TEXT NOT NULL,
  display_name TEXT NOT NULL,
  user_identifier TEXT,
  login_date TEXT NOT NULL,
  time_in TEXT NOT NULL,
  time_out TEXT,
  device_used TEXT,
  ip_address TEXT,
  outcome TEXT NOT NULL DEFAULT 'success',
  failure_reason TEXT,
  flags TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_audit (
  id TEXT PRIMARY KEY,
  user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  user_type TEXT NOT NULL,
  display_name TEXT NOT NULL,
  user_identifier TEXT,
  action_type TEXT NOT NULL,
  action_label TEXT NOT NULL,
  target_type TEXT,
  target_name TEXT,
  details_json TEXT NOT NULL DEFAULT '{}',
  occurred_at TEXT NOT NULL,
  device_used TEXT,
  ip_address TEXT
);

CREATE TABLE IF NOT EXISTS audit_suppressed (
  record_type TEXT NOT NULL,
  record_id TEXT NOT NULL,
  deleted_at TEXT NOT NULL,
  deleted_by TEXT,
  PRIMARY KEY (record_type, record_id)
);

CREATE TABLE IF NOT EXISTS exams (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  instructions TEXT NOT NULL DEFAULT '',
  scheduled_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  time_limit_minutes INTEGER NOT NULL CHECK (time_limit_minutes > 0),
  active INTEGER NOT NULL DEFAULT 0,
  randomize_questions INTEGER NOT NULL DEFAULT 0,
  randomize_options INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 1,
  assigned_index_numbers TEXT NOT NULL DEFAULT '[]',
  created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY,
  exam_id TEXT NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
  question_text TEXT NOT NULL,
  option_a TEXT NOT NULL,
  option_b TEXT NOT NULL,
  option_c TEXT NOT NULL,
  option_d TEXT NOT NULL,
  correct_answer TEXT NOT NULL CHECK (correct_answer IN ('A', 'B', 'C', 'D')),
  rationale TEXT NOT NULL DEFAULT '',
  marks REAL NOT NULL CHECK (marks > 0),
  position INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attempts (
  id TEXT PRIMARY KEY,
  exam_id TEXT NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
  candidate_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  attempt_number INTEGER NOT NULL DEFAULT 1,
  started_at TEXT NOT NULL,
  due_at TEXT NOT NULL,
  submitted_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('in_progress', 'submitted')),
  answers_json TEXT NOT NULL DEFAULT '{}',
  question_order_json TEXT NOT NULL DEFAULT '[]',
  option_orders_json TEXT NOT NULL DEFAULT '{}',
  score REAL,
  total_marks REAL,
  percentage REAL,
  time_spent_seconds INTEGER
);

CREATE TABLE IF NOT EXISTS exam_events (
  id TEXT PRIMARY KEY,
  attempt_id TEXT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
  candidate_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_login_audit_time_in ON login_audit(time_in);
CREATE INDEX IF NOT EXISTS idx_login_audit_session_id ON login_audit(session_id);
CREATE INDEX IF NOT EXISTS idx_activity_audit_occurred_at ON activity_audit(occurred_at);
CREATE INDEX IF NOT EXISTS idx_activity_audit_user_id ON activity_audit(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_suppressed_type ON audit_suppressed(record_type);
CREATE INDEX IF NOT EXISTS idx_questions_exam_id ON questions(exam_id);
CREATE INDEX IF NOT EXISTS idx_attempts_exam_candidate ON attempts(exam_id, candidate_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_attempts_in_progress ON attempts(exam_id, candidate_id) WHERE status = 'in_progress';
CREATE INDEX IF NOT EXISTS idx_events_attempt_id ON exam_events(attempt_id);
