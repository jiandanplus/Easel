# 登录页品牌

把 Logto 托管的登录、注册页改成优创风格：优创标识、工作台的暖色配色、中文标题，语言固定为简体中文。只改 `sign_in_experiences` 的外观字段并新增一条 `custom_phrases`，不碰账号和应用。

- `login.css`、`easel-wordmark.png`、`easel-favicon.png`：源文件。
- `build_sql.py`：生成 `apply.sql`。

已在本机用生产同一镜像（Logto 1.45.0）验证过。上线（需用户确认）：先备份这两张表，在身份库 `easel_logto_preview` 执行 `apply.sql`，再重启 `easel-logto-v2-20261024` 让缓存生效，重启时登录会中断几秒。

底部的 “Powered by Logto” 保留：开源版用 `data-logto-signature-container` 规则保护它，`hide_logto_branding` 也不生效。
