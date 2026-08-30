CREATE TABLE schema_version (
    version INTEGER NOT NULL
);

INSERT INTO schema_version (version) VALUES (26);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    api_call_count INTEGER DEFAULT 0,
    estimated_cost_usd REAL,
    actual_cost_usd REAL,
    cost_status TEXT,
    cost_source TEXT,
    system_prompt TEXT,
    cwd TEXT
);

CREATE TABLE session_model_usage (
    session_id TEXT NOT NULL,
    model TEXT NOT NULL,
    billing_provider TEXT NOT NULL DEFAULT '',
    billing_base_url TEXT NOT NULL DEFAULT '',
    billing_mode TEXT NOT NULL DEFAULT '',
    task TEXT NOT NULL DEFAULT '',
    api_call_count INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    actual_cost_usd REAL NOT NULL DEFAULT 0,
    cost_status TEXT,
    cost_source TEXT
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    content TEXT,
    tool_calls TEXT
);

INSERT INTO sessions (
    id,
    source,
    started_at,
    ended_at,
    input_tokens,
    output_tokens,
    cache_read_tokens,
    cache_write_tokens,
    reasoning_tokens,
    tool_call_count,
    api_call_count,
    estimated_cost_usd,
    actual_cost_usd,
    cost_status,
    cost_source,
    system_prompt,
    cwd
) VALUES (
    'synthetic-hermes-session-01',
    'cli',
    1788102000,
    1788105600,
    1200,
    350,
    800,
    200,
    50,
    7,
    3,
    0.0125,
    0.0105,
    'actual',
    'provider_generation_api',
    'PRIVATE_SYSTEM_PROMPT_SENTINEL',
    'PRIVATE_WORKSPACE_PATH_SENTINEL'
);

INSERT INTO session_model_usage (
    session_id,
    model,
    billing_provider,
    billing_base_url,
    billing_mode,
    task,
    api_call_count,
    input_tokens,
    output_tokens,
    cache_read_tokens,
    cache_write_tokens,
    reasoning_tokens,
    estimated_cost_usd,
    actual_cost_usd,
    cost_status,
    cost_source
) VALUES (
    'synthetic-hermes-session-01',
    'synthetic-model',
    'synthetic-provider',
    'https://synthetic-provider.invalid/v1',
    'synthetic-direct',
    '',
    3,
    1200,
    350,
    800,
    200,
    50,
    0.0125,
    0.0105,
    'actual',
    'provider_generation_api'
);

INSERT INTO messages (id, session_id, content, tool_calls) VALUES (
    1,
    'synthetic-hermes-session-01',
    'PRIVATE_MESSAGE_PAYLOAD_SENTINEL',
    'PRIVATE_TOOL_PAYLOAD_SENTINEL'
);
