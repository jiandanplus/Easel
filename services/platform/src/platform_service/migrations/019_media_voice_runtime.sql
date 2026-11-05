-- Extend M2 original-task GET recovery; 018 remains byte-for-byte unchanged.
ALTER TABLE media_work DROP CONSTRAINT media_work_adapter_check;
ALTER TABLE media_work ADD CONSTRAINT media_work_adapter_check CHECK(adapter IN
 ('newapi_videos_v1','newapi_suno_v1','newapi_dashscope_music_v1',
  'newapi_minimax_voice_v1','newapi_dashscope_voice_v1','newapi_fish_voice_v1'));

-- Strictly parsed private usage facts are evidence, not public prices or tokens.
CREATE TABLE media_protocol_facts (
 job_id text PRIMARY KEY REFERENCES media_jobs(id), attempt_id text NOT NULL,
 binding_hash text NOT NULL CHECK(binding_hash ~ '^[a-f0-9]{64}$'),
 namespace text NOT NULL CHECK(namespace IN ('openai_transcriptions_v1','newapi_minimax_voice_v1','newapi_dashscope_voice_v1')),
 response_hash text NOT NULL CHECK(response_hash ~ '^[a-f0-9]{64}$'),
 facts jsonb NOT NULL CHECK(jsonb_typeof(facts)='object' AND octet_length(facts::text)<=16384),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER media_protocol_facts_immutable BEFORE UPDATE OR DELETE ON media_protocol_facts
 FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
CREATE TRIGGER media_protocol_facts_no_truncate BEFORE TRUNCATE ON media_protocol_facts
 FOR EACH STATEMENT EXECUTE FUNCTION deny_immutable_change();
CREATE FUNCTION check_media_protocol_facts() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE j media_jobs%ROWTYPE;f media_protocol_facts%ROWTYPE;e media_terminal_evidence%ROWTYPE;
BEGIN
 SELECT * INTO j FROM media_jobs WHERE id=NEW.job_id;
 SELECT * INTO f FROM media_protocol_facts WHERE job_id=NEW.job_id;
 IF FOUND AND ((f.attempt_id,f.binding_hash,f.namespace) IS DISTINCT FROM
    (j.attempt_id,j.binding_hash,j.model_definition->>'adapter') OR j.dispatched_at IS NULL OR NOT EXISTS(
    SELECT 1 FROM media_provider_attempts a WHERE a.job_id=j.id AND a.attempt_id=f.attempt_id AND a.binding_hash=f.binding_hash)) THEN
 RAISE EXCEPTION 'protocol facts binding mismatch' USING ERRCODE='23514';END IF;
 SELECT * INTO e FROM media_terminal_evidence WHERE job_id=NEW.job_id;
 IF FOUND AND f.job_id IS NOT NULL AND f.response_hash<>e.evidence_hash THEN
 RAISE EXCEPTION 'protocol facts response mismatch' USING ERRCODE='23514';END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER media_protocol_facts_binding AFTER INSERT ON media_protocol_facts
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_media_protocol_facts();
CREATE CONSTRAINT TRIGGER media_terminal_protocol_facts AFTER INSERT ON media_terminal_evidence
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_media_protocol_facts();

-- SQL clients must satisfy the parser's closed numerical schema.
-- Return false, never SQL NULL (CHECK otherwise treats NULL as success).
CREATE FUNCTION valid_media_protocol_facts(ns text, value jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE key text; amount numeric; incoming numeric; outgoing numeric; total numeric;
BEGIN
 IF jsonb_typeof(value) IS DISTINCT FROM 'object' THEN RETURN false;END IF;
 IF ns='openai_transcriptions_v1' AND value->>'type'='tokens' THEN
   IF NOT value ?& ARRAY['type','input_tokens','output_tokens','total_tokens'] OR
     value - ARRAY['type','input_tokens','output_tokens','total_tokens']<>'{}'::jsonb THEN RETURN false;END IF;
   FOREACH key IN ARRAY ARRAY['input_tokens','output_tokens','total_tokens'] LOOP
     IF jsonb_typeof(value->key) IS DISTINCT FROM 'number' OR
       (value->>key) !~ '^[0-9]+$' THEN RETURN false;END IF;
     amount:=(value->>key)::numeric;
     IF amount<0 OR amount>9223372036854775807 THEN RETURN false;END IF;
   END LOOP;
   incoming:=(value->>'input_tokens')::numeric;outgoing:=(value->>'output_tokens')::numeric;
   total:=(value->>'total_tokens')::numeric;
   RETURN total=incoming+outgoing;
 ELSIF ns='openai_transcriptions_v1' AND value->>'type'='duration' THEN
   IF NOT value ?& ARRAY['type','seconds','rounding'] OR
     value - ARRAY['type','seconds','rounding']<>'{}'::jsonb OR
     value->>'rounding' IS DISTINCT FROM 'ceil_milliseconds_v1' OR
     jsonb_typeof(value->'seconds') IS DISTINCT FROM 'string' OR
     length(value->>'seconds')>64 OR
     (value->>'seconds') !~ '^[+]?[0-9]+([.][0-9]+)?([Ee][+-]?[0-9]+)?$' THEN RETURN false;END IF;
   amount:=(value->>'seconds')::numeric;
   RETURN amount>=0 AND ceil(amount*1000)<=9223372036854775807;
 ELSIF ns IN ('newapi_minimax_voice_v1','newapi_dashscope_voice_v1') THEN
   key:=CASE ns WHEN 'newapi_minimax_voice_v1' THEN 'usage_characters' ELSE 'characters' END;
   IF NOT value ? key OR
     value - (CASE ns WHEN 'newapi_minimax_voice_v1' THEN ARRAY['usage_characters','audio_length_ms'] ELSE ARRAY['characters'] END)<>'{}'::jsonb OR
     jsonb_typeof(value->key) IS DISTINCT FROM 'number' OR (value->>key) !~ '^[0-9]+$' THEN RETURN false;END IF;
   amount:=(value->>key)::numeric;
   IF amount<0 OR amount>9223372036854775807 THEN RETURN false;END IF;
   IF ns='newapi_minimax_voice_v1' AND value ? 'audio_length_ms' THEN
     IF jsonb_typeof(value->'audio_length_ms') IS DISTINCT FROM 'string' OR length(value->>'audio_length_ms')>128 OR
       (value->>'audio_length_ms') !~ '^[+]?[0-9]+([.][0-9]+)?([Ee][+-]?[0-9]+)?$' THEN RETURN false;END IF;
     amount:=(value->>'audio_length_ms')::numeric;
     IF amount<0 OR amount>900000 THEN RETURN false;END IF;
   END IF;
   RETURN true;
 END IF;
 RETURN false;
EXCEPTION WHEN numeric_value_out_of_range OR invalid_text_representation THEN RETURN false;
END $$;
ALTER TABLE media_protocol_facts ADD CONSTRAINT media_protocol_facts_fields
 CHECK (valid_media_protocol_facts(namespace,facts) IS TRUE);
