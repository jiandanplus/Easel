-- apimart answers image requests with a task, so they run through the durable task queue (decision 0016).
ALTER TABLE media_work DROP CONSTRAINT media_work_adapter_check;
ALTER TABLE media_work ADD CONSTRAINT media_work_adapter_check CHECK(adapter IN
 ('newapi_videos_v1','newapi_suno_v1','newapi_dashscope_music_v1','newapi_incho_v1',
  'newapi_minimax_voice_v1','newapi_dashscope_voice_v1','newapi_fish_voice_v1',
  'dashscope_video_v1','ark_video_v1','kling_video_v1','agnes_video_v1','xhs_video_v1','dashscope_music_v1','suno_direct_v1',
  'apimart_image_v1'));
