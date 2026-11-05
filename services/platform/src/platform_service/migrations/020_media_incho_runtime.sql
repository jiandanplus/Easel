-- Extend original-task GET recovery for the explicit Incho 1.0.2 protocol.
-- No model definitions, rates, funding or operator enablement are created.
ALTER TABLE media_work DROP CONSTRAINT media_work_adapter_check;
ALTER TABLE media_work ADD CONSTRAINT media_work_adapter_check CHECK(adapter IN
 ('newapi_videos_v1','newapi_suno_v1','newapi_dashscope_music_v1','newapi_incho_v1',
  'newapi_minimax_voice_v1','newapi_dashscope_voice_v1','newapi_fish_voice_v1'));
