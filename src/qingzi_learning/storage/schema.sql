PRAGMA foreign_keys = ON;

-- One manifest covers the complete Markdown/dashboard set below the fixed root.
-- Pending is committed before the first visible replace; crash recovery checks
-- both this marker and the hashes, never just a completed workflow journal.
CREATE TABLE IF NOT EXISTS reading_publication (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    generation TEXT NOT NULL,
    facts_hash TEXT NOT NULL,
    files_json TEXT NOT NULL,
    pending INTEGER NOT NULL
);

-- Captured from the actual complete staged batch, not reconstructed from an
-- untrusted manifest. Recovery also compares it with current database facts.
CREATE TABLE IF NOT EXISTS reading_output_set (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    generation TEXT NOT NULL,
    paths_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    subject_confidence REAL,
    document_type TEXT NOT NULL,
    grading_mode TEXT,
    teacher_mark_evidence_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    raw_directory TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pages (
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY (document_id, page_number)
);

CREATE TABLE IF NOT EXISTS questions (
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    question_id TEXT NOT NULL,
    question_type TEXT NOT NULL,
    page INTEGER NOT NULL,
    prompt_summary TEXT NOT NULL,
    student_answer TEXT NOT NULL,
    reference_answer TEXT NOT NULL,
    status TEXT NOT NULL,
    decision_source TEXT NOT NULL,
    error_categories_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (document_id, question_id),
    FOREIGN KEY (document_id, page)
        REFERENCES pages(document_id, page_number) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS question_knowledge_points (
    document_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    knowledge_point TEXT NOT NULL,
    PRIMARY KEY (document_id, question_id, knowledge_point),
    FOREIGN KEY (document_id, question_id)
        REFERENCES questions(document_id, question_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS knowledge_stats (
    subject TEXT NOT NULL,
    knowledge_point TEXT NOT NULL,
    first_seen_at TEXT,
    last_seen_at TEXT,
    exposure_count INTEGER NOT NULL DEFAULT 0,
    correct_count INTEGER NOT NULL DEFAULT 0,
    incorrect_count INTEGER NOT NULL DEFAULT 0,
    partial_count INTEGER NOT NULL DEFAULT 0,
    teacher_correct_count INTEGER NOT NULL DEFAULT 0,
    teacher_incorrect_count INTEGER NOT NULL DEFAULT 0,
    model_correct_count INTEGER NOT NULL DEFAULT 0,
    model_incorrect_count INTEGER NOT NULL DEFAULT 0,
    common_error_categories_json TEXT NOT NULL DEFAULT '[]',
    needs_review INTEGER NOT NULL DEFAULT 0,
    review_priority INTEGER NOT NULL DEFAULT 0,
    trend TEXT NOT NULL DEFAULT 'no_data',
    PRIMARY KEY (subject, knowledge_point)
);

CREATE TABLE IF NOT EXISTS processing_jobs (
    document_id TEXT PRIMARY KEY REFERENCES documents(document_id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    knowledge_applied INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_documents_subject ON documents(subject);
CREATE INDEX IF NOT EXISTS idx_questions_document ON questions(document_id);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON processing_jobs(state);

-- Parent decisions overlay the immutable analysis facts. The audit intentionally
-- has no cascading foreign key: a future archive replacement cannot erase it.
CREATE TABLE IF NOT EXISTS parent_reviews (
    document_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    final_status TEXT NOT NULL CHECK(final_status IN ('correct', 'incorrect', 'partial')),
    corrected_answer TEXT NOT NULL,
    note TEXT NOT NULL,
    revision INTEGER NOT NULL,
    confirmed_at TEXT NOT NULL,
    PRIMARY KEY(document_id, question_id),
    FOREIGN KEY(document_id, question_id) REFERENCES questions(document_id, question_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS review_audit (
    audit_id INTEGER PRIMARY KEY,
    document_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    original_json TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    confirmed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_publications (
    document_id TEXT PRIMARY KEY REFERENCES documents(document_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL DEFAULT 1,
    pending INTEGER NOT NULL DEFAULT 1
);

CREATE VIEW IF NOT EXISTS effective_questions AS
SELECT q.document_id, q.question_id, q.question_type, q.page, q.prompt_summary,
       q.student_answer,
       CASE WHEN r.document_id IS NULL OR r.corrected_answer = '' THEN q.reference_answer ELSE r.corrected_answer END AS reference_answer,
       COALESCE(r.final_status, q.status) AS status,
       CASE WHEN r.document_id IS NULL THEN q.decision_source ELSE 'parent' END AS decision_source,
       q.error_categories_json, q.confidence, q.reason,
       q.status AS original_status, q.decision_source AS original_decision_source,
       q.reference_answer AS original_reference_answer,
       COALESCE(r.revision, 0) AS review_revision, r.note AS review_note, r.confirmed_at
FROM questions q LEFT JOIN parent_reviews r
ON r.document_id = q.document_id AND r.question_id = q.question_id;

-- Subject-neutral orchestration journal; subject documents are created only after choice.
CREATE TABLE IF NOT EXISTS workflow_jobs (
    job_id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK(state IN ('capturing', 'spooled', 'analyzing',
        'needs_subject_confirmation', 'needs_review', 'completed', 'pending')),
    subject TEXT,
    knowledge_applied INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
