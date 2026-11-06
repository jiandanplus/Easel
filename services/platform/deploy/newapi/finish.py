#!/usr/bin/env python3
"""Finish the 154 gateway after the administrator has created the root account.

  seed    close registration, keep RetryTimes=0, and register the business token
          the central service already holds, so its model bindings stay unchanged
  switch  issue the api.drawbookai.cn certificate, put nginx on 127.0.0.1:19443
          and retire the old tunnel

Run as root on the server. Token values are read and written here and never printed.
"""
import json
import pathlib
import shutil
import ssl
import subprocess
import sys
import time
import urllib.request

HOST = "api.drawbookai.cn"
URL = f"https://{HOST}:19443"
LOCAL = "http://127.0.0.1:19880"
CERT = pathlib.Path("/etc/letsencrypt/live/easel-gateway/fullchain.pem")
SITE = pathlib.Path("/etc/nginx/sites-enabled/easel-gateway")
OPTIONS = {"RetryTimes": "0", "RegisterEnabled": "false", "PasswordRegisterEnabled": "false",
           "SelfUseModeEnabled": "true"}
# Customer charging lives in the central ledger, so the gateway account must never run dry.
ROOT_QUOTA = 10**12


def sql(text):
    done = subprocess.run(["docker", "exec", "-i", "easel-newapi-pg", "psql", "-U", "newapi", "-d", "newapi",
                           "-v", "ON_ERROR_STOP=1", "-Atq"], input=text, capture_output=True, text=True, check=False)
    if done.returncode:
        raise SystemExit("database step failed")
    return done.stdout.strip()


def quote(value):
    return "'" + value.replace("'", "''") + "'"


def business_secret():
    files = ("/etc/easel-central/execution.json", "/etc/easel-central/media.json")
    secrets = {p["secret"] for f in files for p in json.load(open(f))["providers"].values()}
    if len(secrets) != 1:
        raise SystemExit("expected exactly one business token")
    return secrets.pop()


def status(url, secret=None):
    headers = {"Authorization": "Bearer " + secret} if secret else {}
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                         urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    with opener.open(request, timeout=15) as response:
        return json.load(response)


def seed():
    root = sql("select id from users where role >= 100 and deleted_at is null order by id limit 1;")
    if not root:
        raise SystemExit("root account has not been created yet")
    for key, value in OPTIONS.items():
        sql(f"insert into options(key,value) values({quote(key)},{quote(value)}) "
            "on conflict (key) do update set value=excluded.value;")
    sql(f"update users set quota={ROOT_QUOTA} where id={int(root)};")
    key = business_secret().removeprefix("sk-")
    now = int(time.time())
    if sql(f"select count(*) from tokens where key={quote(key)};") == "0":
        sql('insert into tokens(user_id,key,status,name,created_time,accessed_time,expired_time,remain_quota,'
            'unlimited_quota,model_limits_enabled,model_limits,allow_ips,used_quota,"group",cross_group_retry) '
            f"values({int(root)},{quote(key)},1,'easel',{now},{now},-1,0,true,false,'','',0,'',false);")
    subprocess.run(["docker", "restart", "easel-newapi"], check=True, capture_output=True)
    for _ in range(30):
        try:
            models = status(LOCAL + "/v1/models", "sk-" + key)["data"]
            print(json.dumps({"models": sorted(m["id"] for m in models)}))
            return
        except Exception:
            time.sleep(2)
    raise SystemExit("gateway did not accept the business token")


def switch():
    source = pathlib.Path(__file__).with_name("nginx-gateway.conf")
    if not CERT.exists():
        subprocess.run(["certbot", "certonly", "--webroot", "-w", "/srv/easel/acme", "-d", HOST,
                        "--cert-name", "easel-gateway", "--non-interactive", "--agree-tos",
                        "--register-unsafely-without-email", "--keep-until-expiring"], check=True)
    subprocess.run(["systemctl", "disable", "--now", "easel-gateway-tunnel"], check=True, capture_output=True)
    shutil.copyfile(source, SITE)
    subprocess.run(["nginx", "-t"], check=True, capture_output=True)
    subprocess.run(["systemctl", "reload", "nginx"], check=True)
    time.sleep(2)
    print(json.dumps({"version": status(URL + "/api/status")["data"]["version"]}))


if __name__ == "__main__":
    {"seed": seed, "switch": switch}[sys.argv[1]]()
