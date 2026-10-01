-- Knowledge base (A4): runbook sections and past-incident reports, with embeddings for vector
-- search and a generated tsvector for keyword search. Error codes like "XID 79" need exact
-- matches that embeddings blur, which is why both exist (hybrid search).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE kb_chunks (
    chunk_id      text PRIMARY KEY,
    doc_id        text        NOT NULL,
    doc_kind      text        NOT NULL CHECK (doc_kind IN ('runbook', 'incident')),
    title         text        NOT NULL,
    heading       text        NOT NULL,
    text          text        NOT NULL,
    failure_types text[]      NOT NULL DEFAULT '{}',
    components    text[]      NOT NULL DEFAULT '{}',
    doc_version   text        NOT NULL,
    source        text,
    content_hash  text        NOT NULL,
    embedding     vector(384) NOT NULL,  -- BAAI/bge-small-en-v1.5
    tsv           tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX kb_chunks_embedding ON kb_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX kb_chunks_tsv ON kb_chunks USING gin (tsv);
CREATE INDEX kb_chunks_doc ON kb_chunks (doc_id);
CREATE INDEX kb_chunks_failure_types ON kb_chunks USING gin (failure_types);

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'knowledge_service') THEN
        CREATE ROLE knowledge_service NOLOGIN;
    END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO knowledge_service', current_database());
END
$$;
GRANT USAGE ON SCHEMA public TO knowledge_service;
GRANT SELECT, INSERT, UPDATE, DELETE ON kb_chunks TO knowledge_service;
GRANT SELECT ON kb_chunks TO grafana_reader, incident_detector;
ALTER ROLE knowledge_service SET statement_timeout = '30s';
