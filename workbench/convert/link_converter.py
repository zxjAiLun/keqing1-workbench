from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
import time


def parse_log_url(url: str) -> Dict[str, str]:
    p = urlparse(url)
    host = p.netloc.lower()
    # Some MajSoul links use `maj-soul` (with '-') in the host, e.g.
    # https://game.maj-soul.com/1/?paipu=...
    # Normalize by removing '-' so we can match `majsoul`.
    host_norm = host.replace("-", "")
    qs = parse_qs(p.query)
    if "tenhou.net" in host_norm or "tenhou.net" in host:
        log_id = qs.get("log", [None])[0]
        tw = qs.get("tw", ["0"])[0]
        if not log_id:
            raise RuntimeError(f"invalid tenhou url, missing log id: {url}")
        return {"site": "tenhou", "log_id": log_id, "tw": str(tw)}
    if "mahjongsoul" in host_norm or "majsoul" in host_norm:
        return {"site": "mjsoul"}
    return {"site": "unknown"}


def download_tenhou6_from_tenhou(log_id: str, out_file: str, retries: int = 3) -> None:
    endpoint = f"https://tenhou.net/5/mjlog2json.cgi?{log_id}"
    last_err: Optional[Exception] = None
    headers = {
        "Referer": "https://tenhou.net/",
        # Tenhou seems sensitive to headers and may intermittently return
        # empty/non-JSON payloads. Use the simplest UA possible.
        "User-Agent": "Mozilla/5.0",
    }
    for attempt in range(retries):
        req = Request(endpoint, headers=headers)
        try:
            with urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8", errors="replace").strip()
            if not body:
                raise RuntimeError("tenhou empty response body")
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError as e:
                preview = body[:160].replace("\n", " ")
                raise RuntimeError(f"tenhou non-json response: '{preview}...'") from e
            Path(out_file).parent.mkdir(parents=True, exist_ok=True)
            Path(out_file).write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
            return
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (attempt + 1))
            continue
    Path(out_file).parent.mkdir(parents=True, exist_ok=True)
    if last_err is not None:
        raise last_err
    raise RuntimeError(f"failed to download tenhou log_id={log_id}")


def try_download_mjsoul_via_mjai_reviewer(url: str, out_file: str, reviewer_bin: Optional[str]) -> bool:
    if not reviewer_bin:
        return False
    cmd = [
        reviewer_bin,
        "--no-review",
        "-u",
        url,
        "--tenhou-out",
        out_file,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode == 0 and Path(out_file).exists()


def convert_url_to_tenhou6(url: str, out_file: str, reviewer_bin: Optional[str] = None) -> Dict:
    info = parse_log_url(url)
    site = info["site"]
    if site == "tenhou":
        try:
            download_tenhou6_from_tenhou(info["log_id"], out_file)
            return {"ok": True, "site": "tenhou", "out_file": out_file, "log_id": info["log_id"], "tw": info["tw"]}
        except Exception as e:
            ok = try_download_mjsoul_via_mjai_reviewer(url, out_file, reviewer_bin)
            if ok:
                return {
                    "ok": True,
                    "site": "tenhou",
                    "out_file": out_file,
                    "log_id": info["log_id"],
                    "tw": info["tw"],
                    "engine": "mjai-reviewer",
                }
            return {"ok": False, "site": "tenhou", "out_file": out_file, "message": str(e)}
    if site == "mjsoul":
        ok = try_download_mjsoul_via_mjai_reviewer(url, out_file, reviewer_bin)
        if ok:
            return {"ok": True, "site": "mjsoul", "out_file": out_file, "engine": "mjai-reviewer"}
        return {
            "ok": False,
            "site": "mjsoul",
            "out_file": out_file,
            "message": "mjsoul link cannot be fetched directly in this environment. "
            "Use mjai-reviewer documented browser export (downloadlogs/Majsoul+) to obtain log json, then put file into dataset.",
        }
    return {"ok": False, "site": "unknown", "out_file": out_file, "message": f"unsupported url: {url}"}

