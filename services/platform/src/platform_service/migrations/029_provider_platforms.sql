-- One saved interface per platform on each page, and the native supplier adapters (decision 0016).
ALTER TABLE provider_configs ADD COLUMN platform text NOT NULL DEFAULT 'gateway' CHECK(platform ~ '^[a-z0-9-]{1,32}$');
DROP INDEX provider_configs_current;
CREATE INDEX provider_configs_current ON provider_configs(channel,platform,created_at DESC,id DESC);

ALTER TABLE provider_config_status ADD COLUMN platform text NOT NULL DEFAULT 'gateway' CHECK(platform ~ '^[a-z0-9-]{1,32}$');
ALTER TABLE provider_config_status DROP CONSTRAINT provider_config_status_pkey;
ALTER TABLE provider_config_status ADD PRIMARY KEY(channel,platform);

ALTER TABLE media_work DROP CONSTRAINT media_work_adapter_check;
ALTER TABLE media_work ADD CONSTRAINT media_work_adapter_check CHECK(adapter IN
 ('newapi_videos_v1','newapi_suno_v1','newapi_dashscope_music_v1','newapi_incho_v1',
  'newapi_minimax_voice_v1','newapi_dashscope_voice_v1','newapi_fish_voice_v1',
  'dashscope_video_v1','ark_video_v1','kling_video_v1','agnes_video_v1','dashscope_music_v1','suno_direct_v1'));

-- Suppliers that return no usable request id are tied to the id this service sent.
ALTER TABLE media_http_response_references DROP CONSTRAINT media_http_response_references_check;
ALTER TABLE media_http_response_references ADD CONSTRAINT media_http_response_references_check CHECK(
    (namespace='openai_x_request_id_v1' AND http_request_id ~ '^req_[A-Za-z0-9_-]{1,192}$')
 OR (namespace='newapi_x_oneapi_request_id_v1' AND http_request_id ~ '^[0-9]{23}[A-Za-z0-9]{16}$')
 OR (namespace='easel_client_request_v1' AND http_request_id ~ '^[A-Za-z0-9_.:-]{1,128}$'));
ALTER TABLE media_terminal_evidence DROP CONSTRAINT media_terminal_reference_kind;
ALTER TABLE media_terminal_evidence ADD CONSTRAINT media_terminal_reference_kind CHECK(
    (reference_kind='object_id' AND provider_response_id IS NOT NULL AND http_reference_namespace IS NULL AND http_reference_value IS NULL)
 OR (reference_kind='http_request_id' AND provider_response_id IS NULL AND http_reference_namespace IS NOT NULL AND http_reference_value IS NOT NULL AND
     ((http_reference_namespace='openai_x_request_id_v1' AND http_reference_value ~ '^req_[A-Za-z0-9_-]{1,192}$')
       OR (http_reference_namespace='newapi_x_oneapi_request_id_v1' AND http_reference_value ~ '^[0-9]{23}[A-Za-z0-9]{16}$')
       OR (http_reference_namespace='easel_client_request_v1' AND http_reference_value ~ '^[A-Za-z0-9_.:-]{1,128}$'))));
ALTER TABLE media_receipts DROP CONSTRAINT media_receipt_reference_kind;
ALTER TABLE media_receipts ADD CONSTRAINT media_receipt_reference_kind CHECK(
    (reference_kind='object_id' AND provider_response_id IS NOT NULL AND http_reference_namespace IS NULL AND http_reference_value IS NULL)
 OR (reference_kind='http_request_id' AND provider_response_id IS NULL AND http_reference_namespace IS NOT NULL AND http_reference_value IS NOT NULL AND
     ((http_reference_namespace='openai_x_request_id_v1' AND http_reference_value ~ '^req_[A-Za-z0-9_-]{1,192}$')
       OR (http_reference_namespace='newapi_x_oneapi_request_id_v1' AND http_reference_value ~ '^[0-9]{23}[A-Za-z0-9]{16}$')
       OR (http_reference_namespace='easel_client_request_v1' AND http_reference_value ~ '^[A-Za-z0-9_.:-]{1,128}$'))));
