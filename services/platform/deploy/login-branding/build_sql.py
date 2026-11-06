#!/usr/bin/env python3
"""Write apply.sql: the 优创 look for the Logto sign-in pages (brand only, no accounts)."""
import base64
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def data_uri(name):
    return "data:image/png;base64," + base64.b64encode((HERE / name).read_bytes()).decode()


def literal(value):
    return "'" + value.replace("'", "''") + "'"


logo = data_uri("easel-wordmark.png")
branding = {"logoUrl": logo, "darkLogoUrl": logo, "favicon": data_uri("easel-favicon.png")}
color = {"primaryColor": "#B74F30", "isDarkModeEnabled": False, "darkPrimaryColor": "#D9764F"}
language = {"autoDetect": False, "fallbackLanguage": "zh-CN"}
phrases = {"description": {"sign_in_to_your_account": "登录优创", "create_your_account": "注册优创账号"}}
(HERE / "apply.sql").write_text(f"""BEGIN;
UPDATE sign_in_experiences SET
  branding = {literal(json.dumps(branding))}::jsonb,
  color = {literal(json.dumps(color))}::jsonb,
  language_info = {literal(json.dumps(language))}::jsonb,
  custom_css = {literal((HERE / "login.css").read_text())}
WHERE tenant_id = 'default' AND id = 'default';
INSERT INTO custom_phrases (tenant_id, id, language_tag, translation)
VALUES ('default', 'easelzhcn', 'zh-CN', {literal(json.dumps(phrases, ensure_ascii=False))}::jsonb)
ON CONFLICT (tenant_id, language_tag) DO UPDATE SET translation = EXCLUDED.translation;
COMMIT;
""")
