"""Decide what the form filler may do with a form field. Pure functions, no browser needed.

Categories returned by `classify`:
  profile:<key>   fill from profile_fields.json (first_name, last_name, full_name, preferred_name,
                  email, phone, phone_country_code, phone_type, linkedin, github, website,
                  location, address_line1, city, state, postal_code, country)
  file:cv / file:cover   upload the drafted PDF
  never           a question the user must answer personally (work authorization, sponsorship,
                  demographics, salary, start date, consent, ...). Highlight, never fill.
  choice          any radio/checkbox/select not covered above. Left for the user.
  free_text       a text box / textarea we have no deterministic value for. Candidate for a
                  drafted answer (grounded in the profile), never auto-submitted.
"""

from __future__ import annotations

import re

NEVER_PATTERNS = [
    r"authori[sz]ed to work|legally (authori|eligible|permitted)|work authori|right to work|eligible to work|employment eligibility",
    r"sponsor|\bvisa\b|immigration|\bopt\b|\bcpt\b|\bh-?1b\b|work permit",
    r"citizen|clearance|security clearance|\bitar\b|export control",
    r"gender|\brace\b|ethnic|hispanic|latino|veteran|disabilit|sexual orientation|transgender|pronoun|demographic|equal opportunity|\beeo\b|\bage\b|date of birth",
    r"salary|compensation|pay expectation|expected pay|desired pay|hourly rate|expected rate",
    r"start date|earliest start|available to start|when can you start|notice period|availability",
    r"privacy|consent|i agree|acknowledge|terms (and|&) conditions|terms of (use|service)|gdpr|accept the",
    r"how did you hear|where did you (hear|find)|referred by|referral|source",
    r"criminal|convict|background check|drug test",
    r"relocat|willing to (travel|work)|on-?site|in-?office",
    r"signature|e-?sign",
]

PROFILE_PATTERNS: list[tuple[str, str]] = [
    ("preferred_name", r"^preferred name\b"),
    ("first_name", r"^(preferred |legal )?first name|given name"),
    ("last_name", r"^(preferred |legal )?last name|family name|surname"),
    ("email", r"e-?mail"),
    ("phone_country_code", r"country code|dial(?:ing)? code|phone code"),
    ("phone_type", r"phone type|type of phone"),
    ("phone", r"\b(phone|mobile|telephone|cell)\b"),
    ("linkedin", r"linkedin"),
    ("github", r"github"),
    ("website", r"\bwebsite\b|\bportfolio\b|personal (site|url|page)|^url$|other url"),
    ("address_line1", r"\bstreet\b|address line|^address$|address 1|mailing address|home address"),
    ("postal_code", r"\bzip\b|postal code|postcode|post code"),
    ("city", r"\bcity\b"),
    ("state", r"\b(state|province|region)\b"),
    ("country", r"\bcountry\b"),
    ("full_name", r"^(your |full |legal )?name\b|^name$"),
    ("location", r"^location\b|current location|where are you (based|located)"),
]

_never = [re.compile(p, re.I) for p in NEVER_PATTERNS]
_profile = [(k, re.compile(p, re.I)) for k, p in PROFILE_PATTERNS]


def is_never(text: str) -> bool:
    return any(p.search(text or "") for p in _never)


def classify(field: dict) -> str:
    """`field` has: tag, type, label, ctx (nearby heading text, optional)."""
    tag = (field.get("tag") or "").lower()
    ftype = (field.get("type") or "").lower()
    label = (field.get("label") or "").strip()
    haystack = f"{label} {field.get('ctx') or ''}".strip()

    if ftype == "file":
        low = haystack.lower()
        if re.search(r"cover letter|covering letter|motivation", low):
            return "file:cover"
        if re.search(r"resume|cv|curriculum", low):
            return "file:cv"
        return "choice"

    # Never-fill topics win over everything else, including choice widgets.
    if is_never(haystack):
        return "never"

    for key, pattern in _profile:
        if pattern.search(label):
            return f"profile:{key}"

    kind = (field.get("kind") or "").lower()
    role = (field.get("role") or "").lower()
    popup = (field.get("popup") or "").lower()
    # Dropdowns, button groups and dates are choices unless a profile rule above already claimed them.
    if kind in {"radio", "checkbox", "select", "combobox", "buttons", "multiselect", "date"} or ftype in {"radio", "checkbox", "date"} or tag == "select" or role in {"combobox", "checkbox", "radio"} or popup in {"listbox", "true", "menu"}:
        return "choice"

    if tag == "textarea" or ftype in {"text", "", "search", "input"}:
        return "free_text" if label else "choice"
    if ftype in {"email", "tel", "url"}:
        return "choice"
    return "choice"
