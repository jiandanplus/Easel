"""Ask a supplier which voices an account can use. Listing is not a billed synthesis call."""
import re

import httpx

VOICE_ID = r"[A-Za-z0-9_.()-](?:[A-Za-z0-9_.() -]{0,62}[A-Za-z0-9_.()-])?"
MAX_VOICES = 500
ATTEMPTS = 3
_FISH_OWN_PAGES = 3
_MINIMAX_AUTH_CODES = (1004, 2049)


class _Refused(Exception):
    def __init__(self, state):
        self.state = state


def _entry(voice_id, name, kind):
    if not isinstance(voice_id, str) or not re.fullmatch(VOICE_ID, voice_id, re.ASCII):
        return None
    shown = name if isinstance(name, str) and name.strip() and len(name) <= 128 and all(ord(c) >= 32 for c in name) else voice_id
    return {"id": voice_id, "name": shown.strip(), "kind": kind}


async def _call(client, method, url, **extra):
    for attempt in range(ATTEMPTS):
        try:
            response = await client.request(method, url, timeout=10, **extra)
            break
        except httpx.HTTPError:
            if attempt == ATTEMPTS - 1:
                raise _Refused("unreachable") from None
    if response.status_code in (401, 403):
        raise _Refused("auth_failed")
    try:
        body = response.json() if response.status_code == 200 else None
    except ValueError:
        body = None
    return body if isinstance(body, dict) else {}


async def _fish(client, base_url, secret, options):
    headers = {"Authorization": "Bearer " + secret}

    def usable(items, kind):
        return [_entry(item.get("_id"), item.get("title"), kind) for item in items
                if isinstance(item, dict) and item.get("state") in (None, "trained")]

    found = []
    for number in range(1, _FISH_OWN_PAGES + 1):
        body = await _call(client, "GET", base_url + "/model", headers=headers, params={"self": "true", "page_size": 100, "page_number": number})
        items = body.get("items")
        if not isinstance(items, list):
            break
        found += usable(items, "mine")
        if not body.get("has_more"):
            break
    # A new account owns no voices, so the list also offers popular Chinese ones unless a search says otherwise.
    public = {"title": options["voice_query"]} if options.get("voice_query") else {"language": "zh"}
    body = await _call(client, "GET", base_url + "/model", headers=headers, params={**public, "page_size": 100})
    if isinstance(body.get("items"), list):
        found += usable(body["items"], "public")
    return found


async def _minimax(client, base_url, secret, options):
    group = options.get("group_id")
    body = await _call(client, "POST", base_url + "/v1/get_voice", headers={"Authorization": "Bearer " + secret},
                       params={"GroupId": group} if group else None, json={"voice_type": "all"})
    status = body.get("base_resp")
    if isinstance(status, dict) and status.get("status_code") in _MINIMAX_AUTH_CODES:
        raise _Refused("auth_failed")
    found = []
    for field, kind in (("voice_cloning", "cloned"), ("voice_generation", "generated"), ("system_voice", "system")):
        items = body.get(field)
        if isinstance(items, list):
            found += [_entry(item.get("voice_id"), item.get("voice_name"), kind) for item in items if isinstance(item, dict)]
    return found


async def _dashscope(client, base_url, secret, options):
    body = await _call(client, "POST", base_url + "/api/v1/services/audio/tts/customization", headers={"Authorization": "Bearer " + secret},
                       json={"model": "voice-enrollment", "input": {"action": "list_voice", "page_index": 0, "page_size": 100}})
    output = body.get("output")
    items = output.get("voice_list") if isinstance(output, dict) else None
    if not isinstance(items, list):
        return []
    return [_entry(item.get("voice_id"), None, "cloned") for item in items if isinstance(item, dict) and item.get("status") in (None, "OK")]


_LISTERS = {"fish": _fish, "minimax": _minimax, "dashscope": _dashscope}


async def list_voices(client, kind, base_url, secret, options):
    """Return (failure state or None, voices). An answer that cannot be read is an empty list."""
    try:
        rows = await _LISTERS[kind](client, base_url, secret, options)
    except _Refused as refused:
        return refused.state, []
    unique, seen = [], set()
    for row in rows:
        if row is not None and row["id"] not in seen:
            seen.add(row["id"])
            unique.append(row)
    return None, unique[:MAX_VOICES]
