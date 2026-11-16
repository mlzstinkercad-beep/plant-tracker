"""
The AI part of the app (no screen code in here, so it is easy to test).

Two services are supported:
  * Google Gemini  - free key from aistudio.google.com  (uses only built-in Python tools)
  * Anthropic Claude - paid key (needs:  pip install anthropic)

Speed tricks used here:
  * small photos (the app shrinks them before they get here)
  * short answers (the prompt asks for brief text, so the AI finishes sooner)
  * minimal "thinking" on the fast Gemini models
  * a model that is missing is remembered and never tried again
"""

import base64
import json
import os
import urllib.error
import urllib.request

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Free Gemini models that accept photos. The first one that works is used.
GEMINI_MODELS = {
    "fast": ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"],
    "accurate": ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"],
}
CLAUDE_MODELS = {"fast": "claude-haiku-5-5", "accurate": "claude-sonnet-5-5"}

# Remembered between calls while the app is running.
_state = {"thinking": True, "dead_models": set()}


class ServiceBusy(Exception):
    """The free service says 'too many requests' (limit reached or overloaded)."""


class AIError(Exception):
    """Something went wrong that the user should be told about."""


# ----------------------------------------------------------------------------
# Prompts
# ----------------------------------------------------------------------------
PLANT_PROMPT = """You are an expert botanist helping students and home gardeners in India.
Study the plant photo(s) carefully: leaf shape and colour, spots, wilting, soil, pot size.
If there are several photos, they all show the SAME plant.
__HINT__
Reply with ONLY one JSON object, no other text:

{
  "plant_name": "common name, or 'Unknown plant'",
  "confidence": "low | medium | high",
  "health_score": number 0-100,
  "health_status": "Healthy | Needs attention | Unhealthy",
  "growth_stage": "Seedling | Young plant | Mature | Flowering | Fruiting | Dormant",
  "estimated_height_cm": number or null (only if there is a size clue),
  "observations": ["2-3 things you can see"],
  "problems": ["problems seen, or empty list"],
  "needs": {"water": "...", "sunlight": "...", "soil": "...", "fertilizer": "..."},
  "water_every_days": whole number,
  "fertilize_every_days": whole number,
  "next_steps": ["2-3 actions for this week"],
  "photo_tip": "one tip for a better photo, or empty string if the photo is good",
  "fun_fact": "one short fact"
}

RULES
- Be brief: every text value at most 12 words; lists have at most 3 items.
- Write all text values in __LANGUAGE__ using simple words. Keep JSON keys in English.
- For confidence, health_status and growth_stage use EXACTLY one of the English words listed.
- Do not guess. If the plant is unclear, use 'Unknown plant', confidence low, and say in photo_tip what photo would help.
- health_score: 90+ excellent, 70-89 good, 45-69 needs care, below 45 poor. Be consistent.
- If there is no plant: plant_name 'No plant found', health_score 0, explain in observations."""

FUN_PROMPT = """You are a friendly, kind comedian for a school science fair.
Look at the picture and reply with ONLY one JSON object, no other text:

{
  "what_i_see": "one short sentence describing the picture",
  "recognized": "name of the meme format, cartoon/movie/game character, animal, food, object, landmark or artwork, or empty string",
  "joke": "one short, clean, funny joke about it",
  "caption_idea": "a short funny caption for the picture"
}

RULES
- Write all text values in __LANGUAGE__ using simple words. Keep JSON keys in English.
- Memes: name the meme format only from its layout, text or caption.
- NEVER identify a real person from their face, even if they look famous. Do not guess who anyone is.
  Describe only what is visible (clothes, expression, pose, setting). Use a person's name only if it is
  written in the picture (caption, name tag, jersey) or the user gave it below.
- Joke about the situation, the object or the vibe. Never joke about anyone's body, face, age, skin colour,
  religion, disability, family, or politics. No adult content, no insults, no meanness.
- If the picture shows a child, keep the joke gentle and only about the situation.
- Keep it short: each text value at most 25 words.
__HINT__"""


def build_prompt(template, language_name, hint=""):
    hint = (hint or "").strip()[:80]
    if hint and template is PLANT_PROMPT:
        hint_line = (
            f"The user says this plant is: {hint}. Use that unless the photo clearly shows otherwise."
        )
    elif hint:
        hint_line = f"The user says this picture shows: {hint}. You may use that name in the joke."
    else:
        hint_line = ""
    return template.replace("__LANGUAGE__", language_name).replace("__HINT__", hint_line)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def parse_json(text):
    """Pull the JSON object out of the AI's reply, even if it adds extra words."""
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("The AI did not return a readable answer. Please try again.")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise ValueError("The AI answer was cut off or garbled. Please try again.") from e


def _b64(data):
    return base64.standard_b64encode(data).decode("ascii")


def _error_detail(e):
    try:
        return e.read().decode("utf-8", errors="ignore")[:300]
    except Exception:
        return ""


def gemini_models(quality):
    env = os.environ.get("GEMINI_MODEL")
    models = ([env] if env else []) + GEMINI_MODELS.get(quality, GEMINI_MODELS["fast"])
    live = [m for m in models if m not in _state["dead_models"]]
    return live or models


def _thinking_level(model):
    # Google's docs: flash-lite supports 'minimal'; the bigger Flash models need at least 'low'.
    return "minimal" if "lite" in model else "low"


# ----------------------------------------------------------------------------
# Gemini (free)
# ----------------------------------------------------------------------------
def ask_gemini(api_key, jpegs, prompt, quality="fast", temperature=0.2, timeout=60):
    parts = [{"inline_data": {"mime_type": "image/jpeg", "data": _b64(j)}} for j in jpegs]
    parts.append({"text": prompt})

    busy = False
    last = "no model was available"
    for model in gemini_models(quality):
        attempts = [True, False] if _state["thinking"] else [False]
        for use_thinking in attempts:
            config = {
                "responseMimeType": "application/json",
                "temperature": temperature,
                "maxOutputTokens": 4096,
            }
            if use_thinking:
                config["thinkingConfig"] = {"thinkingLevel": _thinking_level(model)}
            body = json.dumps(
                {"contents": [{"parts": parts}], "generationConfig": config}
            ).encode("utf-8")
            request = urllib.request.Request(
                GEMINI_URL.format(model=model),
                data=body,
                headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = _error_detail(e)
                if e.code == 404:  # this model name is gone - never try it again
                    _state["dead_models"].add(model)
                    last = f"model {model} not found"
                    break
                if e.code in (429, 503):  # busy: try the next model, then give up
                    busy = True
                    break
                if e.code >= 500:
                    last = f"Google server error {e.code}"
                    break
                if e.code == 400 and use_thinking and "think" in detail.lower():
                    _state["thinking"] = False  # this setting is not accepted - retry without it
                    continue
                raise AIError(f"Gemini error {e.code}: {detail}") from e
            except urllib.error.URLError as e:
                raise AIError(
                    f"Cannot reach the AI service. Check your internet. ({e.reason})"
                ) from e
            except (TimeoutError, ConnectionError) as e:
                raise AIError("The AI service took too long. Please try again.") from e

            candidates = data.get("candidates") or []
            content_parts = (candidates[0].get("content", {}) if candidates else {}).get("parts", [])
            text = "".join(p.get("text", "") for p in content_parts if not p.get("thought"))
            if not text.strip():
                last = "the AI could not answer for this picture"
                break
            return parse_json(text)

    if busy:
        raise ServiceBusy()
    raise AIError(f"No answer from Gemini ({last}).")


# ----------------------------------------------------------------------------
# Claude (paid)
# ----------------------------------------------------------------------------
def ask_claude(api_key, jpegs, prompt, quality="fast", temperature=0.2, timeout=60):
    import anthropic

    client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
    content = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": _b64(j)},
        }
        for j in jpegs
    ]
    content.append({"type": "text", "text": prompt})
    try:
        response = client.messages.create(
            model=os.environ.get("CLAUDE_MODEL") or CLAUDE_MODELS.get(quality, CLAUDE_MODELS["fast"]),
            max_tokens=1800,
            temperature=temperature,
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.RateLimitError as e:
        raise ServiceBusy() from e
    return parse_json(response.content[0].text)


def ask(provider, api_key, jpegs, prompt, quality="fast", temperature=0.2):
    if provider == "gemini":
        return ask_gemini(api_key, jpegs, prompt, quality, temperature)
    return ask_claude(api_key, jpegs, prompt, quality, temperature)


def analyze_plant(provider, api_key, jpegs, language_name, quality="fast", hint=""):
    prompt = build_prompt(PLANT_PROMPT, language_name, hint)
    return ask(provider, api_key, jpegs, prompt, quality, temperature=0.2)


def joke_about_picture(provider, api_key, jpeg, language_name, quality="fast", hint=""):
    prompt = build_prompt(FUN_PROMPT, language_name, hint)
    return ask(provider, api_key, [jpeg], prompt, quality, temperature=0.9)
