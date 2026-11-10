-- Remaining supplier migration (decision 0016): MaaS video and supplier voice ids.
ALTER TABLE media_work DROP CONSTRAINT media_work_adapter_check;
ALTER TABLE media_work ADD CONSTRAINT media_work_adapter_check CHECK(adapter IN
 ('newapi_videos_v1','newapi_suno_v1','newapi_dashscope_music_v1','newapi_incho_v1',
  'newapi_minimax_voice_v1','newapi_dashscope_voice_v1','newapi_fish_voice_v1',
  'dashscope_video_v1','ark_video_v1','kling_video_v1','agnes_video_v1','xhs_video_v1','dashscope_music_v1','suno_direct_v1'));

-- Registration evidence stores a fixed-shape reference; some suppliers' voice ids do not fit it.
CREATE TABLE provider_voice_aliases (
    provider_scope_hash text NOT NULL CHECK(provider_scope_hash ~ '^[a-f0-9]{64}$'),
    reference text NOT NULL CHECK(reference ~ '^task_[A-Za-z0-9]{32}$'),
    provider_voice_id text NOT NULL CHECK(length(provider_voice_id) BETWEEN 1 AND 512 AND provider_voice_id !~ '[[:space:][:cntrl:]]'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(provider_scope_hash,reference)
);
CREATE TRIGGER provider_voice_aliases_immutable BEFORE UPDATE OR DELETE ON provider_voice_aliases
FOR EACH ROW EXECUTE FUNCTION deny_immutable_change();
