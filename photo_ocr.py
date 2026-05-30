"""Photo OCR for Dubai property listing brochures.

Uses Gemini Vision (free tier) — fallback to OpenRouter free vision models.
Returns parser_v2-compatible dict from a photo URL or bytes.

Designed for: off-plan brochure images with embedded text (price, building,
area, BR, sqft, handover) — common in Telegram channels.
"""
from __future__ import annotations
import os
import re
import json
import base64
import urllib.request
import urllib.parse
from typing import Optional


# ─── Gemini vision (primary) ──────────────────────────────────────────────
def _gemini_keys():
    keys = []
    for k in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3"):
        v = os.environ.get(k, "").strip()
        if v:
            keys.append(v)
    return keys


def _openrouter_keys():
    keys = []
    for k in ("OPENROUTER_API_KEY", "OPENROUTER_API_KEY_2", "OPENROUTER_API_KEY_3"):
        v = os.environ.get(k, "").strip()
        if v:
            keys.append(v)
    return keys


VISION_PROMPT = """You are reading a Telegram property listing brochure image
for Dubai real estate. The image may contain text (OCR), floorplan,
infographic, or property photo. Extract all structured data you can see.

Return a single JSON object with these exact keys (use null when not visible):
{
  "deal_type": "sale" | "rent" | null,
  "property_type": "apartment" | "studio" | "villa" | "townhouse" | "penthouse" | "duplex" | "office" | "shop" | "retail" | "plot" | "hotel" | null,
  "area": "<user-friendly Dubai community name like 'Dubai Marina', 'JLT', 'Downtown Dubai'>" | null,
  "building": "<exact building/project name>" | null,
  "bedrooms": 0..10 | null,
  "bathrooms": 0..10 | null,
  "size_sqft": <number> | null,
  "size_sqm": <number> | null,
  "floor": <integer> | null,
  "view": "sea|park|marina|burj|...|null",
  "furnishing": "furnished" | "unfurnished" | "semi-furnished" | null,
  "is_off_plan": true | false | null,
  "handover_date": "YYYY-MM" | null,
  "price": <integer AED, current asking> | null,
  "price_original": <integer AED, launch/developer price if different> | null,
  "agent_name": "<name>" | null,
  "phone": "<+971...>" | null,
  "confidence": 0.0..1.0
}

Rules:
- For prices: "1.5M AED" = 1500000, "AED 1.91M" = 1910000, never use letters.
- For studio: bedrooms = 0.
- Use null when the image doesn't show the field — DON'T guess.
- Set confidence based on image readability (0.0=can't read, 1.0=crystal clear).
- Output ONLY the JSON object, no prose, no markdown."""


def _resolve_telegram_file_id(file_id: str, bot_token: Optional[str] = None) -> Optional[str]:
    """Resolve a Telegram file_id into a direct HTTPS URL via getFile API."""
    token = bot_token or os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        return None
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}",
            method="GET")
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        if data.get("ok"):
            path = data["result"]["file_path"]
            return f"https://api.telegram.org/file/bot{token}/{path}"
    except Exception as e:
        print(f"[photo_ocr] getFile err: {e}")
    return None


def _looks_like_tg_file_id(s: str) -> bool:
    """Heuristic: Telegram file_ids are long base64-ish strings, not URLs."""
    return (s and not s.startswith("http") and len(s) > 40
            and "/" not in s and "." not in s)


def _download_image(url_or_id: str, timeout: int = 15,
                     bot_token: Optional[str] = None) -> Optional[bytes]:
    if not url_or_id:
        return None
    # Resolve Telegram file_id if needed
    if _looks_like_tg_file_id(url_or_id):
        resolved = _resolve_telegram_file_id(url_or_id, bot_token)
        if not resolved:
            return None
        url_or_id = resolved
    try:
        req = urllib.request.Request(url_or_id, headers={
            "User-Agent": "Mozilla/5.0 (compatible; ResaleBot/1.0)"
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            if len(data) < 200:  # too small to be useful image
                return None
            if len(data) > 5_000_000:  # too big (>5MB)
                return data[:5_000_000]
            return data
    except Exception:
        return None


def _strip_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    s = raw.strip()
    if "```" in s:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.DOTALL)
        if m:
            s = m.group(1)
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(s[i:j+1])
    except Exception:
        try:
            cleaned = re.sub(r",\s*([}\]])", r"\1", s[i:j+1])
            return json.loads(cleaned)
        except Exception:
            return None


def _gemini_vision(img_bytes: bytes, prompt: str = VISION_PROMPT,
                    timeout: int = 30) -> Optional[dict]:
    keys = _gemini_keys()
    if not keys or not img_bytes:
        return None
    b64 = base64.b64encode(img_bytes).decode("ascii")
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {
                    "mime_type": "image/jpeg",
                    "data": b64,
                }},
            ]
        }],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 800,
        }
    }
    data = json.dumps(payload).encode("utf-8")
    for key in keys:
        url = ("https://generativelanguage.googleapis.com/v1beta/"
                "models/gemini-2.5-flash:generateContent?key=" + key)
        try:
            req = urllib.request.Request(url, data=data, method="POST",
                                          headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read())
            cands = resp.get("candidates") or []
            if not cands:
                continue
            parts = cands[0].get("content", {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts)
            parsed = _strip_json(text)
            if parsed:
                return parsed
        except Exception as e:
            print(f"[photo_ocr] gemini err: {e}")
            continue
    return None


def _openrouter_vision(img_bytes: bytes, prompt: str = VISION_PROMPT,
                        timeout: int = 30) -> Optional[dict]:
    """Fallback: OpenRouter free vision models."""
    keys = _openrouter_keys()
    if not keys or not img_bytes:
        return None
    b64 = base64.b64encode(img_bytes).decode("ascii")
    data_url = f"data:image/jpeg;base64,{b64}"
    payload = {
        "model": "meta-llama/llama-3.2-11b-vision-instruct:free",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
        "max_tokens": 800,
        "temperature": 0.1,
    }
    body = json.dumps(payload).encode("utf-8")
    for key in keys:
        try:
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=body, method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {key}",
                    "HTTP-Referer": "https://t.me/dubai_resale_bot",
                    "X-Title": "Dubai Resale Bot",
                })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read())
            choices = resp.get("choices") or []
            if not choices:
                continue
            text = choices[0].get("message", {}).get("content") or ""
            parsed = _strip_json(text)
            if parsed:
                return parsed
        except Exception as e:
            print(f"[photo_ocr] openrouter err: {e}")
            continue
    return None


def extract_from_image(image_url_or_bytes, *, prefer: str = "gemini") -> Optional[dict]:
    """Public API. Extract listing fields from image URL or raw bytes.

    Args:
      image_url_or_bytes: HTTPS URL OR raw bytes
      prefer: 'gemini' (default, best free quality) or 'openrouter' (llama-3.2-vision)
    Returns:
      Parsed listing dict OR None if extraction failed.
    """
    if isinstance(image_url_or_bytes, str):
        img = _download_image(image_url_or_bytes)
    else:
        img = image_url_or_bytes
    if not img:
        return None
    # Try preferred first, then fallback
    fn1, fn2 = (_gemini_vision, _openrouter_vision) if prefer == "gemini" else (
                _openrouter_vision, _gemini_vision)
    result = fn1(img)
    if not result:
        result = fn2(img)
    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python photo_ocr.py <image_url>")
        sys.exit(1)
    import pprint
    pprint.pprint(extract_from_image(sys.argv[1]))
