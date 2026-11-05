#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SNAPINSTA REVERSE-ENGINEERED ENGINE & SDK
=========================================
Engenharia reversa do https://snapinsta.com.br/ (Proxy Visolix API) +
Enriquecimento de Metadados via TelegramBot Link-Preview Header +
Decodificador Matemático de Shortcodes/EFG da CDN do Instagram +
Protocolo Auto-Healing (Bundle JS Scanner + Playwright Off-Screen Fallback).

Capacidades mapeadas:
  1. Reels & Vídeos (/reel/<code>/, /reels/<code>/, /tv/<code>/) -> MP4 (H.264 + AAC) + Thumbnail + Caption/Likes
  2. Posts & Carrosséis Completos (/p/<code>/) -> Todas as Fotos em 4K UHD (4096px) e Vídeos do carrossel
  3. Foto de Perfil / Avatar (@usuario ou instagram.com/<usuario>/) -> Avatar JPEG + Stats do Perfil
  4. Feed Recente de Perfil (@usuario) -> Avatar + Últimos ~15 Reels/Posts do perfil com Shortcode reconstruído
  5. Zero Limite Diário -> Bypassa 100% a trava de 20 downloads/dia do localStorage ("snapinsta_downloads")
"""

from __future__ import annotations

import argparse
import base64
import html as html_lib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
SELECTORS_PATH = BASE_DIR / "selectors.json"
DEFAULT_OUT_DIR = BASE_DIR / "downloads"

IG_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

DEFAULT_SELECTORS = {
    "base_url": "https://snapinsta.com.br",
    "api_endpoint": "https://snapinsta.com.br/api/download.php",
    "fallback_visolix_url": "https://base44.app/api/apps/6a5d6264bd9a67071621e0cb/functions/visolixDownload",
    "local_storage_limit_key": "snapinsta_downloads",
    "input_selector": "input#instagram-url",
    "submit_selector": "form button[type='submit']",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}


def load_selectors() -> Dict[str, Any]:
    if SELECTORS_PATH.exists():
        try:
            data = json.loads(SELECTORS_PATH.read_text(encoding="utf-8"))
            merged = dict(DEFAULT_SELECTORS)
            merged.update(data)
            return merged
        except Exception:
            pass
    return dict(DEFAULT_SELECTORS)


def media_id_to_shortcode(media_id: int) -> str:
    """Converte um Media ID numérico de 19 dígitos do Instagram para o Shortcode Base64 oficial."""
    if media_id <= 0:
        return ""
    code = ""
    while media_id > 0:
        media_id, rem = divmod(media_id, 64)
        code = IG_ALPHABET[rem] + code
    return code


def shortcode_to_media_id(shortcode: str) -> int:
    """Converte um Shortcode oficial do Instagram para o Media ID numérico."""
    media_id = 0
    for ch in shortcode:
        idx = IG_ALPHABET.find(ch)
        if idx == -1:
            break
        media_id = media_id * 64 + idx
    return media_id


def decode_cdn_url_metadata(cdn_url: str, thumb_url: str = "") -> Dict[str, Any]:
    """
    Decodifica os parâmetros internos `efg` e `ig_cache_key` assinados na URL da CDN
    `scontent.cdninstagram.com` para revelar resolução original, duração, tipo (avatar/carrossel/reel)
    e o Shortcode oficial do post.
    """
    info: Dict[str, Any] = {
        "is_avatar": False,
        "vencode_tag": None,
        "duration_s": None,
        "asset_id": None,
        "media_id": None,
        "shortcode": None,
        "estimated_resolution": None,
    }
    combined = f"{cdn_url} {thumb_url}"

    if "/t51.2885-19/" in combined or "/t51.82787-19/" in combined:
        info["is_avatar"] = True

    m_efg = re.search(r"[?&]efg=([^&\s]+)", cdn_url)
    if m_efg:
        try:
            raw_efg = urllib.parse.unquote(m_efg.group(1))
            pad = "=" * (-len(raw_efg) % 4)
            efg_json = json.loads(base64.urlsafe_b64decode(raw_efg + pad).decode("utf-8", "ignore"))
            vtag = efg_json.get("vencode_tag", "")
            info["vencode_tag"] = vtag
            info["duration_s"] = efg_json.get("duration_s")
            info["asset_id"] = efg_json.get("xpv_asset_id")
            if "profile_pic" in vtag:
                info["is_avatar"] = True
            m_res = re.search(r"\b(4096|2160|1440|1080|720|640|480)\b", vtag)
            if m_res:
                info["estimated_resolution"] = f"{m_res.group(1)}p"
        except Exception:
            pass

    m_ck = re.search(r"[?&]ig_cache_key=([^&\s]+)", combined)
    if m_ck:
        try:
            b64_part = urllib.parse.unquote(m_ck.group(1)).split(".")[0]
            pad = "=" * (-len(b64_part) % 4)
            decoded_ck = base64.urlsafe_b64decode(b64_part + pad).decode("utf-8", "ignore")
            m_digits = re.match(r"^(\d{18,19})", decoded_ck)
            if m_digits:
                mid = int(m_digits.group(1))
                info["media_id"] = str(mid)
                info["shortcode"] = media_id_to_shortcode(mid)
        except Exception:
            pass

    return info


def normalize_instagram_input(raw_input: str) -> Dict[str, str]:
    """
    Normaliza qualquer entrada (@username, username puro, link de reel, post, stories ou perfil)
    para uma URL canônica do Instagram e classifica o tipo da entrada.
    """
    s = raw_input.strip()
    if not s:
        raise ValueError("Entrada vazia. Informe um link do Instagram ou @usuario.")

    if s.startswith("@") or (not s.startswith("http") and "instagram.com" not in s and "/" not in s):
        username = s.lstrip("@").strip()
        return {
            "input": raw_input,
            "normalized_url": f"https://www.instagram.com/{username}/",
            "input_type": "profile",
            "username": username,
            "shortcode": "",
        }

    if not s.startswith("http"):
        s = "https://" + s

    parsed = urllib.parse.urlparse(s)
    path_parts = [p for p in parsed.path.split("/") if p]

    for idx, part in enumerate(path_parts):
        if part.lower() in ("reel", "reels", "p", "tv") and idx + 1 < len(path_parts):
            kind_map = {"reel": "reel", "reels": "reel", "p": "post", "tv": "tv"}
            shortcode = path_parts[idx + 1]
            prefix = "reel" if kind_map[part.lower()] == "reel" else ("tv" if part.lower() == "tv" else "p")
            username = path_parts[0] if idx > 0 else ""
            return {
                "input": raw_input,
                "normalized_url": f"https://www.instagram.com/{prefix}/{shortcode}/",
                "input_type": kind_map[part.lower()],
                "username": username,
                "shortcode": shortcode,
            }

    if path_parts and path_parts[0].lower() == "stories" and len(path_parts) >= 2:
        username = path_parts[1]
        story_id = path_parts[2] if len(path_parts) >= 3 else ""
        norm = f"https://www.instagram.com/stories/{username}/{story_id + '/' if story_id else ''}"
        return {
            "input": raw_input,
            "normalized_url": norm,
            "input_type": "stories",
            "username": username,
            "shortcode": story_id,
        }

    if path_parts:
        username = path_parts[0]
        return {
            "input": raw_input,
            "normalized_url": f"https://www.instagram.com/{username}/",
            "input_type": "profile",
            "username": username,
            "shortcode": "",
        }

    raise ValueError(f"Não foi possível interpretar o link do Instagram: {raw_input}")


def fetch_instagram_metadata(normalized_url: str) -> Dict[str, Any]:
    """
    Usa o User-Agent de Link Preview ('TelegramBot (like TwitterBot)') para extrair
    instantaneamente título, autor, @username, likes, comentários, data, legenda (caption)
    e seguidores sem sofrer bloqueio HTTP 429 do Instagram.
    """
    meta: Dict[str, Any] = {
        "og_type": None,
        "og_title": None,
        "og_description": None,
        "og_url": None,
        "og_image": None,
        "author_username": None,
        "author_full_name": None,
        "likes": None,
        "comments": None,
        "post_date": None,
        "caption": None,
        "followers": None,
        "following": None,
        "posts_count": None,
        "canonical_shortcode": None,
    }
    req = urllib.request.Request(
        normalized_url,
        headers={
            "User-Agent": "TelegramBot (like TwitterBot)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = resp.read().decode("utf-8", "ignore")
    except Exception as e:
        meta["metadata_error"] = str(e)
        return meta

    tags = re.findall(r'<meta[^>]+(?:property|name)="([^"]+)"[^>]+content="([^"]*)"', body)
    tag_map: Dict[str, str] = {}
    for k, v in tags:
        tag_map[k.lower()] = html_lib.unescape(v)

    meta["og_type"] = tag_map.get("og:type")
    meta["og_title"] = tag_map.get("og:title") or tag_map.get("twitter:title")
    meta["og_description"] = tag_map.get("og:description") or tag_map.get("description")
    meta["og_url"] = tag_map.get("og:url") or tag_map.get("al:android:url")
    meta["og_image"] = tag_map.get("og:image") or tag_map.get("twitter:image")

    if meta["og_url"]:
        m_sc = re.search(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)", meta["og_url"])
        if m_sc:
            meta["canonical_shortcode"] = m_sc.group(1)
        m_user_url = re.search(r"instagram\.com/([^/]+)/(?:p|reel|reels|tv)/", meta["og_url"])
        if m_user_url:
            meta["author_username"] = m_user_url.group(1)

    tw_title = tag_map.get("twitter:title") or tag_map.get("og:title") or ""
    m_author = re.search(r"^(.+?)\s*\(@([A-Za-z0-9._]+)\)", tw_title)
    if m_author:
        meta["author_full_name"] = m_author.group(1).strip()
        meta["author_username"] = m_author.group(2).strip()

    desc = meta["og_description"] or ""
    m_prof = re.search(
        r"([0-9.,KMB]+)\s+Followers,\s*([0-9.,KMB]+)\s+Following,\s*([0-9.,KMB]+)\s+Posts",
        desc,
        re.I,
    )
    if m_prof:
        meta["followers"] = m_prof.group(1)
        meta["following"] = m_prof.group(2)
        meta["posts_count"] = m_prof.group(3)

    m_post = re.match(
        r"^([0-9.,KMB]+)\s+likes?,\s*([0-9.,KMB]+)\s+comments?\s*-\s*([A-Za-z0-9._]+)\s+on\s+([^:]+):\s*\"?(.*)\"?$",
        desc,
        re.S | re.I,
    )
    if m_post:
        meta["likes"] = m_post.group(1)
        meta["comments"] = m_post.group(2)
        if not meta["author_username"]:
            meta["author_username"] = m_post.group(3)
        meta["post_date"] = m_post.group(4).strip()
        raw_cap = m_post.group(5).strip()
        if raw_cap.endswith('"') or raw_cap.endswith(".”"):
            raw_cap = raw_cap[:-1].strip()
        meta["caption"] = raw_cap

    return meta


def auto_heal_endpoint(selectors: Dict[str, Any]) -> str:
    base_url = selectors.get("base_url", "https://snapinsta.com.br")
    ua = selectors.get("user_agent", DEFAULT_SELECTORS["user_agent"])
    try:
        req = urllib.request.Request(base_url, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8", "ignore")
        m_js = re.search(r'src="(/assets/index-[^"]+\.js)"', html)
        if m_js:
            js_url = base_url.rstrip("/") + m_js.group(1)
            req_js = urllib.request.Request(js_url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req_js, timeout=15) as rj:
                js_code = rj.read().decode("utf-8", "ignore")
            m_api = re.search(r'"(/api/[a-zA-Z0-9_.-]+\.php)"', js_code)
            if m_api:
                new_ep = base_url.rstrip("/") + m_api.group(1)
                selectors["api_endpoint"] = new_ep
                return new_ep
    except Exception:
        pass
    return selectors.get("api_endpoint", DEFAULT_SELECTORS["api_endpoint"])


def call_snapinsta_playwright_fallback(target_url: str, selectors: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"status": False, "message": "Playwright não instalado para fallback off-screen."}

    captured: Dict[str, Any] = {}
    base_url = selectors.get("base_url", "https://snapinsta.com.br")
    limit_key = selectors.get("local_storage_limit_key", "snapinsta_downloads")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--window-position=-2400,0", "--window-size=1280,800", "--mute-audio"],
        )
        context = browser.new_context(
            user_agent=selectors.get("user_agent", DEFAULT_SELECTORS["user_agent"])
        )
        page = context.new_page()

        def handle_response(response):
            if "/api/" in response.url or "visolixDownload" in response.url:
                try:
                    data = response.json()
                    if isinstance(data, dict) and "media" in data:
                        captured.update(data)
                except Exception:
                    pass

        page.on("response", handle_response)
        page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
        page.evaluate(f"() => localStorage.removeItem('{limit_key}')")
        inp = page.locator(selectors.get("input_selector", "input#instagram-url"))
        inp.fill(target_url)
        btn = page.locator(selectors.get("submit_selector", "form button[type='submit']"))
        btn.click()

        for _ in range(30):
            if captured:
                break
            page.wait_for_timeout(500)

        browser.close()

    return captured if captured else {"status": False, "message": "Timeout no fallback Playwright."}


def call_snapinsta_api(target_url: str, use_playwright_fallback: bool = True) -> Dict[str, Any]:
    selectors = load_selectors()
    endpoint = selectors.get("api_endpoint", DEFAULT_SELECTORS["api_endpoint"])
    ua = selectors.get("user_agent", DEFAULT_SELECTORS["user_agent"])
    payload = json.dumps({"url": target_url}).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": ua,
        "Origin": "https://snapinsta.com.br",
        "Referer": "https://snapinsta.com.br/",
    }

    for attempt in range(2):
        try:
            req = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=25) as resp:
                raw = resp.read().decode("utf-8", "ignore")
                data = json.loads(raw)
                if isinstance(data, dict):
                    return data
        except urllib.error.HTTPError as he:
            if attempt == 0 and he.code in (404, 405, 500, 502, 503):
                endpoint = auto_heal_endpoint(selectors)
                continue
            return {"status": False, "message": f"HTTP Error {he.code}: {he.reason}"}
        except Exception as e:
            if attempt == 0:
                endpoint = auto_heal_endpoint(selectors)
                continue
            if use_playwright_fallback:
                return call_snapinsta_playwright_fallback(target_url, selectors)
            return {"status": False, "message": str(e)}

    if use_playwright_fallback:
        return call_snapinsta_playwright_fallback(target_url, selectors)
    return {"status": False, "message": "Falha ao consultar Snapinsta API."}
