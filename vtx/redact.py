"""Strip credentials and signed URLs from text before it is logged or stored."""
import re

COOKIE_NAMES = (
    "SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "buvid3", "buvid4", "sid",
    "sessionid", "sessionid_ss", "sid_tt", "sid_guard", "uid_tt", "odin_tt", "ttwid",
    "passport_csrf_token", "msToken", "s_v_web_id", "__ac_signature",
)
_COOKIE = re.compile(r"\b(" + "|".join(map(re.escape, COOKIE_NAMES)) + r")=([^;\s&\"']+)")
_TOKEN = re.compile(r"\b(?:sk-[\w-]{8,}|Bearer\s+\S+)")
_URL = re.compile(r"(https?://)([^/\s\"'<>?#]+)[^\s\"'<>]*")


def redact(text: str) -> str:
    text = _COOKIE.sub(r"\1=***", text)
    text = _TOKEN.sub("[credential removed]", text)
    # keep only scheme + host: signed media URLs carry tokens in path/query
    return _URL.sub(r"\1\2/…", text)
