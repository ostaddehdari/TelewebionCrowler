-- Independent Telewebion HTTP-first catalog. This is a locally inferred schema,
-- NOT a copy of Telewebion's private database schema.
CREATE TABLE IF NOT EXISTS entities (
 kind text NOT NULL, entity_id text NOT NULL, title text NOT NULL DEFAULT '',
 subtitle text, content_type text, channel_descriptor text, is_clip boolean,
 image_ref text, raw jsonb NOT NULL, field_count integer NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (kind,entity_id)
);
CREATE INDEX IF NOT EXISTS idx_entity_kind_title ON entities(kind,title);
CREATE INDEX IF NOT EXISTS idx_entity_channel ON entities(channel_descriptor,kind);
CREATE INDEX IF NOT EXISTS idx_entity_updated ON entities(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_entity_kind_clip ON entities(kind,is_clip);
CREATE TABLE IF NOT EXISTS links (
 parent_kind text NOT NULL, parent_id text NOT NULL,
 child_kind text NOT NULL, child_id text NOT NULL,
 relation text NOT NULL DEFAULT '',
 PRIMARY KEY (parent_kind,parent_id,child_kind,child_id,relation)
);
CREATE INDEX IF NOT EXISTS idx_links_child ON links(child_kind,child_id);
CREATE TABLE IF NOT EXISTS api_responses (
 request_key text PRIMARY KEY, endpoint text NOT NULL, request_params jsonb NOT NULL,
 url text NOT NULL, payload jsonb NOT NULL, payload_sha256 text NOT NULL,
 fetched_at timestamptz NOT NULL DEFAULT now(), duration_ms integer NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_api_endpoint ON api_responses(endpoint,fetched_at DESC);
CREATE TABLE IF NOT EXISTS crawl_tasks (
 task_key text PRIMARY KEY, task_type text NOT NULL, params jsonb NOT NULL,
 cursor_offset integer NOT NULL DEFAULT 0, page_size integer NOT NULL DEFAULT 0,
 state text NOT NULL DEFAULT 'queued', priority integer NOT NULL DEFAULT 50,
 attempts integer NOT NULL DEFAULT 0, fetched_pages integer NOT NULL DEFAULT 0,
 discovered_records integer NOT NULL DEFAULT 0,
 last_signature text, last_error text, last_http integer,
 last_run timestamptz, next_run timestamptz NOT NULL DEFAULT now(),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_task_due ON crawl_tasks(state,next_run,priority,created_at);
CREATE TABLE IF NOT EXISTS crawl_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 happened_at timestamptz NOT NULL DEFAULT now(),
 task_key text, level text NOT NULL, message text NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_time ON crawl_events(happened_at DESC);
