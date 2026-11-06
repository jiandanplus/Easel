# New API on the 154 host

Replaces the Volcano gateway, which stopped responding on 2026-11-04 and was not renewed. Its database, channels and supplier keys had no off-host copy and are lost; channels are entered again by hand.

The central service keeps `https://api.drawbookai.cn:19443` and the same business token. Both feed the model `binding_hash` (`execution_config.py`), so changing either would mean republishing every model.

| Piece | Where |
| --- | --- |
| Stack | `/srv/easel/newapi` (compose project `easel-newapi`, `.env` is 0600, secrets generated on the server) |
| New API | `calciumion/new-api` rc.45 by digest, `127.0.0.1:19880` |
| TLS for the central service | nginx `127.0.0.1:19443`, certificate `easel-gateway`; `/etc/hosts` maps the name to 127.0.0.1 |
| 1Panel website | OpenResty on 18480/18443 (closed to the outside by `easel-1panel-guard`), site `api.drawbookai.cn`, proxy to 19880. nginx keeps ports 80 and 443 because accounts and login live there. Open 1Panel with `ssh -N -L 127.0.0.1:18880:127.0.0.1:18080 easel-zhiyin`; `1pctl user-info` shows the entrance |
| Public certificate | Issued and renewed by 1Panel over HTTP, pushed to `/srv/easel/panel-cert`, then nginx reloads. The internal 19443 keeps its certbot certificate, so a failed 1Panel renewal cannot reach the central service. Health watch covers both |
| Admin page | https://api.drawbookai.cn/console: host nginx asks for a password, then forwards to the 1Panel website (`nginx-console.conf`); the fallback is `ssh -N -L 127.0.0.1:18887:127.0.0.1:19880 easel-zhiyin`, then http://127.0.0.1:18887 |

Status 2026-11-06: steps 1-7 are done. Only the DeepSeek channel exists; the two image models have no channel yet.

Order, as root on the server:

1. `./bootstrap.sh` starts the stack.
2. The administrator opens the admin page, creates the root account and adds the channels. Supplier keys stay in that form.
3. `python3 finish.py seed` closes registration, keeps `RetryTimes=0`, and registers the business token.
4. Point the public `api` A record at 154.217.245.179. Let's Encrypt needs it.
5. `python3 finish.py switch` issues the certificate, moves nginx onto 19443 and disables `easel-gateway-tunnel`.
6. In 1Panel install OpenResty on ports 18480/18443, create the reverse proxy website to 127.0.0.1:19880, apply for the certificate with push folder `/srv/easel/panel-cert` and script `systemctl reload nginx`, then turn on the site's HTTPS with "HTTP stays reachable" so certificate checks keep working. Port 80 hands 1Panel's challenges to 18480, so run step 7 once with the certbot certificate paths if the push folder is still empty.
7. `python3 finish.py console` opens the admin page on public 443 behind an nginx password. Everything on that name, `/v1` included, needs the password; the central service keeps using 19443. The password lands in `/root/newapi-console-access.txt`; move it off the server. It is kept on the Mac at `~/Easel-build/newapi-console-access.txt`. To change it, delete `/etc/nginx/easel-gateway-console.htpasswd` and run the step again. To close the page, delete `/etc/nginx/sites-enabled/easel-gateway-console` and reload nginx.

`backup/newapi_backup.py` seals a daily database dump (without request logs) to its own R2 prefix (`newapi`). The envelope kind is `central` because the frozen sealing helper accepts only `central` and `logto`; open one with the same private key and kind. The 2026-11-06 copy was fetched from R2 and restored into a temporary database. The weekly drill restores it too and fails if channels or tokens are missing; the hourly Mac watch alerts when it goes stale.
