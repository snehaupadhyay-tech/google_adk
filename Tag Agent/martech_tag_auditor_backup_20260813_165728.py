#!/usr/bin/env python3
"""Enterprise End-to-End Martech Audit Platform.

Features:
- Crawls sitemap and internal links.
- Static or Playwright-rendered page audits.
- Safe Playwright journey audit with cookie acceptance.
- Detects hidden, destructive, account-gated, region-selector and external
  clicks and logs each one with a reason (Skipped_Interactions tab).
- --track-skipped-interactions safely *tracks* (rather than only logs)
  three of those categories, without doing anything irreversible:
    * Hidden       -> tries to reveal the element via the mobile/hamburger
                      menu toggle first, then clicks it if it becomes visible.
    * Auth_Gated   -> clicks the link far enough to observe the pre-auth
                      analytics hit fired on click, then returns to the
                      original page WITHOUT entering credentials.
    * External_Link-> clicks the link far enough to observe the outbound
                      click-tracking beacon, then returns to the original
                      page WITHOUT interacting with the external site.
  Region_Selector items continue to be handled by --region as before.
  Destructive actions are never tracked, even with this flag.
- FIX: JourneyEvent.url now reflects the DESTINATION url reached after a
  click (e.g. a "...?show=thankyou" conversion page), with the originating
  journey page preserved in `notes`. Previously the event was always
  attributed back to the source page, so conversion/thank-you pages and
  their Adobe/GA4 hits never showed up as their own row in Journey_Audit /
  Network_Logs even though the network capture correctly saw the hit.
- Expanded, Pitney-Bowes-aware PAGE_TYPE_PATTERNS (Industries, ThankYou,
  Support, Resources, Newsroom, Company, API, Careers, Region, expanded
  Product patterns) plus explicit ThankYou detection via query string
  (e.g. "?show=thankyou", "thank-you", "thankyou") since many lead-gen
  conversions are only identifiable by query parameter, not path.
- Captures GA4, Adobe Analytics, dataLayer and network activity.
- Full martech network log capture during journey audit.
- Network_Logs tab in Excel and JSON export.
- Skipped_Interactions tab (with Tracked / Tracked Notes columns).
- Optional --region flag to auto-select a locale when a region/country/
  language selector is detected.
- Dry-run form filling by default.
- Website-level score and recommendations only (no page-level scores).
- Exports JSON and formatted Excel reports.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urldefrag, unquote, urljoin, urlparse

import pandas as pd

try:
    import requests
    from requests.exceptions import RequestException
except ImportError:
    print("Missing dependency: requests. Run: pip install requests")
    sys.exit(1)

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing dependency: beautifulsoup4. Run: pip install beautifulsoup4 lxml")
    sys.exit(1)

try:
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill
except ImportError:
    load_workbook = None
    Alignment = Font = PatternFill = None

try:
    from rich.console import Console
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    Console = None

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

console = Console() if RICH_AVAILABLE else None
DEFAULT_OUTPUT_DIR = r"C:\Users\Sneha.Upadhyay\Desktop\Tag Agent\audit_reports"

# ---------------------------------------------------------------------------
# Platform detection registry (static HTML / script text matching)
# ---------------------------------------------------------------------------

PLATFORMS = {
    "Google Analytics 4": {
        "vendor": "Google", "category": "Analytics", "priority": "Critical",
        "patterns": [r"google-analytics\.com/g/collect", r"analytics\.google\.com/g/collect", r"G-[A-Z0-9]{6,}", r"gtag\("],
    },
    "Google Tag Manager": {
        "vendor": "Google", "category": "Tag Management", "priority": "Critical",
        "patterns": [r"googletagmanager\.com/gtm\.js", r"GTM-[A-Z0-9]{5,}", r"dataLayer\.push", r"window\.dataLayer"],
    },
    "Adobe Analytics": {
        "vendor": "Adobe", "category": "Analytics", "priority": "High",
        "patterns": [r"\.sc\.omtrdc\.net", r"\.2o7\.net", r"/b/ss/", r"AppMeasurement\.js", r"s\.t\(\)", r"s\.tl\("],
    },
    "Adobe Launch": {
        "vendor": "Adobe", "category": "Tag Management", "priority": "High",
        "patterns": [r"assets\.adobedtm\.com", r"_satellite\.", r"adobedtm\.com"],
    },
    "Meta Pixel": {
        "vendor": "Meta", "category": "Advertising", "priority": "High",
        "patterns": [r"facebook\.com/tr", r"connect\.facebook\.net", r"fbq\("],
    },
    "LinkedIn Insight Tag": {
        "vendor": "LinkedIn", "category": "Advertising", "priority": "High",
        "patterns": [r"snap\.licdn\.com", r"px\.ads\.linkedin\.com", r"lintrk\(", r"_linkedin_partner_id"],
    },
    "Microsoft/Bing Ads": {
        "vendor": "Microsoft", "category": "Advertising", "priority": "Medium",
        "patterns": [r"bat\.bing\.com", r"uetq\.push", r"window\.uetq"],
    },
    "HubSpot": {
        "vendor": "HubSpot", "category": "Marketing Automation", "priority": "High",
        "patterns": [r"js\.hs-scripts\.com", r"_hsq\.push", r"hbspt\.forms\.create", r"hsforms\.net"],
    },
    "Marketo": {
        "vendor": "Adobe/Marketo", "category": "Marketing Automation", "priority": "High",
        "patterns": [r"munchkin\.marketo\.net", r"Munchkin\.init", r"mktoForm", r"\.marketo\.com"],
    },
    "Pardot": {
        "vendor": "Salesforce", "category": "Marketing Automation", "priority": "High",
        "patterns": [r"pi\.pardot\.com", r"go\.pardot\.com", r"piTracker", r"piAId"],
    },
    "OneTrust": {
        "vendor": "OneTrust", "category": "Consent Management", "priority": "Critical",
        "patterns": [r"cdn\.cookielaw\.org", r"OptanonWrapper", r"OneTrust"],
    },
    "Cookiebot": {
        "vendor": "Usercentrics", "category": "Consent Management", "priority": "Critical",
        "patterns": [r"consent\.cookiebot\.com", r"Cookiebot", r"CookieConsent"],
    },
    "Microsoft Clarity": {
        "vendor": "Microsoft", "category": "CRO / Session Recording", "priority": "Medium",
        "patterns": [r"clarity\.ms", r"clarity\("],
    },
    "Hotjar": {
        "vendor": "Hotjar", "category": "CRO / Session Recording", "priority": "Medium",
        "patterns": [r"hotjar\.com", r"hj\(", r"hotjar-\d+"],
    },
    "Segment": {
        "vendor": "Twilio", "category": "CDP", "priority": "High",
        "patterns": [r"cdn\.segment\.com", r"analytics\.track", r"analytics\.identify", r"api\.segment"],
    },
}

# ---------------------------------------------------------------------------
# Network-log URL patterns for matching martech requests during journey audit
# ---------------------------------------------------------------------------

MARTECH_URL_PATTERNS: Dict[str, Dict[str, str]] = {
    # Analytics
    r"google-analytics\.com/g/collect": {"vendor": "Google", "category": "Analytics", "platform": "Google Analytics 4"},
    r"analytics\.google\.com/g/collect": {"vendor": "Google", "category": "Analytics", "platform": "Google Analytics 4"},
    r"\.sc\.omtrdc\.net": {"vendor": "Adobe", "category": "Analytics", "platform": "Adobe Analytics"},
    r"\.2o7\.net": {"vendor": "Adobe", "category": "Analytics", "platform": "Adobe Analytics"},
    r"/b/ss/": {"vendor": "Adobe", "category": "Analytics", "platform": "Adobe Analytics"},
    # Tag Management
    r"googletagmanager\.com/gtm\.js": {"vendor": "Google", "category": "Tag Management", "platform": "Google Tag Manager"},
    r"googletagmanager\.com/gtag": {"vendor": "Google", "category": "Tag Management", "platform": "Google Tag Manager"},
    r"assets\.adobedtm\.com": {"vendor": "Adobe", "category": "Tag Management", "platform": "Adobe Launch"},
    # Advertising
    r"facebook\.com/tr": {"vendor": "Meta", "category": "Advertising", "platform": "Meta Pixel"},
    r"connect\.facebook\.net": {"vendor": "Meta", "category": "Advertising", "platform": "Meta Pixel"},
    r"snap\.licdn\.com": {"vendor": "LinkedIn", "category": "Advertising", "platform": "LinkedIn Insight Tag"},
    r"px\.ads\.linkedin\.com": {"vendor": "LinkedIn", "category": "Advertising", "platform": "LinkedIn Insight Tag"},
    r"bat\.bing\.com": {"vendor": "Microsoft", "category": "Advertising", "platform": "Microsoft/Bing Ads"},
    r"doubleclick\.net": {"vendor": "Google", "category": "Advertising", "platform": "Google Ads / DoubleClick"},
    r"googlesyndication\.com": {"vendor": "Google", "category": "Advertising", "platform": "Google AdSense"},
    r"googleadservices\.com": {"vendor": "Google", "category": "Advertising", "platform": "Google Ads"},
    r"google\.com/pagead": {"vendor": "Google", "category": "Advertising", "platform": "Google Ads"},
    r"ads\.twitter\.com": {"vendor": "X / Twitter", "category": "Advertising", "platform": "X Ads Pixel"},
    r"static\.ads-twitter\.com": {"vendor": "X / Twitter", "category": "Advertising", "platform": "X Ads Pixel"},
    r"analytics\.twitter\.com": {"vendor": "X / Twitter", "category": "Advertising", "platform": "X Analytics"},
    r"tiktok\.com/i18n/pixel": {"vendor": "TikTok", "category": "Advertising", "platform": "TikTok Pixel"},
    r"analytics\.tiktok\.com": {"vendor": "TikTok", "category": "Advertising", "platform": "TikTok Pixel"},
    r"sc-static\.net": {"vendor": "Snapchat", "category": "Advertising", "platform": "Snapchat Pixel"},
    r"tr\.snapchat\.com": {"vendor": "Snapchat", "category": "Advertising", "platform": "Snapchat Pixel"},
    r"cm\.g\.doubleclick\.net": {"vendor": "Google", "category": "Advertising", "platform": "Campaign Manager"},
    r"ad\.doubleclick\.net": {"vendor": "Google", "category": "Advertising", "platform": "Campaign Manager"},
    r"idsync\.rlcdn\.com": {"vendor": "LiveRamp", "category": "Advertising", "platform": "LiveRamp"},
    r"insight\.adsrvr\.org": {"vendor": "The Trade Desk", "category": "Advertising", "platform": "The Trade Desk"},
    # Marketing Automation
    r"js\.hs-scripts\.com": {"vendor": "HubSpot", "category": "Marketing Automation", "platform": "HubSpot"},
    r"hsforms\.net": {"vendor": "HubSpot", "category": "Marketing Automation", "platform": "HubSpot"},
    r"forms\.hubspot\.com": {"vendor": "HubSpot", "category": "Marketing Automation", "platform": "HubSpot"},
    r"munchkin\.marketo\.net": {"vendor": "Adobe/Marketo", "category": "Marketing Automation", "platform": "Marketo"},
    r"\.marketo\.com": {"vendor": "Adobe/Marketo", "category": "Marketing Automation", "platform": "Marketo"},
    r"pi\.pardot\.com": {"vendor": "Salesforce", "category": "Marketing Automation", "platform": "Pardot"},
    r"go\.pardot\.com": {"vendor": "Salesforce", "category": "Marketing Automation", "platform": "Pardot"},
    r"app\.salesforce\.com": {"vendor": "Salesforce", "category": "Marketing Automation", "platform": "Salesforce"},
    r"mc\.sendgrid\.net": {"vendor": "Twilio", "category": "Marketing Automation", "platform": "SendGrid"},
    r"tracking\.klaviyo\.com": {"vendor": "Klaviyo", "category": "Marketing Automation", "platform": "Klaviyo"},
    r"static\.klaviyo\.com": {"vendor": "Klaviyo", "category": "Marketing Automation", "platform": "Klaviyo"},
    r"api\.intercom\.io": {"vendor": "Intercom", "category": "Marketing Automation", "platform": "Intercom"},
    r"widget\.intercom\.io": {"vendor": "Intercom", "category": "Marketing Automation", "platform": "Intercom"},
    r"js\.driftt\.com": {"vendor": "Drift", "category": "Marketing Automation", "platform": "Drift"},
    r"api\.drift\.com": {"vendor": "Drift", "category": "Marketing Automation", "platform": "Drift"},
    # Consent Management
    r"cdn\.cookielaw\.org": {"vendor": "OneTrust", "category": "Consent Management", "platform": "OneTrust"},
    r"geolocation\.onetrust\.com": {"vendor": "OneTrust", "category": "Consent Management", "platform": "OneTrust"},
    r"privacyportal\.onetrust\.com": {"vendor": "OneTrust", "category": "Consent Management", "platform": "OneTrust"},
    r"consent\.cookiebot\.com": {"vendor": "Usercentrics", "category": "Consent Management", "platform": "Cookiebot"},
    r"cookiebot\.com": {"vendor": "Usercentrics", "category": "Consent Management", "platform": "Cookiebot"},
    r"app\.usercentrics\.eu": {"vendor": "Usercentrics", "category": "Consent Management", "platform": "Usercentrics"},
    r"api\.trustarc\.com": {"vendor": "TrustArc", "category": "Consent Management", "platform": "TrustArc"},
    r"consent\.trustarc\.com": {"vendor": "TrustArc", "category": "Consent Management", "platform": "TrustArc"},
    r"api\.didomi\.io": {"vendor": "Didomi", "category": "Consent Management", "platform": "Didomi"},
    # CRO / Session Recording
    r"clarity\.ms": {"vendor": "Microsoft", "category": "CRO / Session Recording", "platform": "Microsoft Clarity"},
    r"hotjar\.com": {"vendor": "Hotjar", "category": "CRO / Session Recording", "platform": "Hotjar"},
    r"static\.hotjar\.com": {"vendor": "Hotjar", "category": "CRO / Session Recording", "platform": "Hotjar"},
    r"fullstory\.com": {"vendor": "FullStory", "category": "CRO / Session Recording", "platform": "FullStory"},
    r"logrocket\.com": {"vendor": "LogRocket", "category": "CRO / Session Recording", "platform": "LogRocket"},
    r"inspect\.clarity\.ms": {"vendor": "Microsoft", "category": "CRO / Session Recording", "platform": "Microsoft Clarity"},
    r"heap\.io": {"vendor": "Heap", "category": "CRO / Session Recording", "platform": "Heap Analytics"},
    r"heapanalytics\.com": {"vendor": "Heap", "category": "CRO / Session Recording", "platform": "Heap Analytics"},
    r"cdn\.heapanalytics\.com": {"vendor": "Heap", "category": "CRO / Session Recording", "platform": "Heap Analytics"},
    r"cdn\.mouseflow\.com": {"vendor": "Mouseflow", "category": "CRO / Session Recording", "platform": "Mouseflow"},
    r"a\.mouseflow\.com": {"vendor": "Mouseflow", "category": "CRO / Session Recording", "platform": "Mouseflow"},
    r"cdn\.luckyorange\.com": {"vendor": "Lucky Orange", "category": "CRO / Session Recording", "platform": "Lucky Orange"},
    r"io\.luckyorange\.com": {"vendor": "Lucky Orange", "category": "CRO / Session Recording", "platform": "Lucky Orange"},
    r"d\.crazyegg\.com": {"vendor": "Crazy Egg", "category": "CRO / Session Recording", "platform": "Crazy Egg"},
    r"script\.crazyegg\.com": {"vendor": "Crazy Egg", "category": "CRO / Session Recording", "platform": "Crazy Egg"},
    # CDP
    r"cdn\.segment\.com": {"vendor": "Twilio", "category": "CDP", "platform": "Segment"},
    r"api\.segment\.io": {"vendor": "Twilio", "category": "CDP", "platform": "Segment"},
    r"api\.segment\.com": {"vendor": "Twilio", "category": "CDP", "platform": "Segment"},
    r"cdn\.amplitude\.com": {"vendor": "Amplitude", "category": "CDP", "platform": "Amplitude"},
    r"api\.amplitude\.com": {"vendor": "Amplitude", "category": "CDP", "platform": "Amplitude"},
    r"cdn\.mxpnl\.com": {"vendor": "Mixpanel", "category": "CDP", "platform": "Mixpanel"},
    r"api\.mixpanel\.com": {"vendor": "Mixpanel", "category": "CDP", "platform": "Mixpanel"},
    r"t\.rudderstack\.com": {"vendor": "RudderStack", "category": "CDP", "platform": "RudderStack"},
    r"cdn\.rudderlabs\.com": {"vendor": "RudderStack", "category": "CDP", "platform": "RudderStack"},
    r"api\.customer\.io": {"vendor": "Customer.io", "category": "CDP", "platform": "Customer.io"},
}

# ---------------------------------------------------------------------------
# Page-type classification patterns (Pitney-Bowes-aware, but generic enough
# for most B2B / B2C / ecommerce sites)
# ---------------------------------------------------------------------------

PAGE_TYPE_PATTERNS = {
    "Homepage": [r"^/$", r"/home/?$"],
    "Product": [
        r"/product", r"/products", r"/shop", r"/item", r"/sku",
        r"/shipping-software", r"/postage-meters", r"/mailing-systems",
        r"/sendpro", r"/pitneyship", r"/mailroom-software",
        r"/mailroom-equipment", r"/parcel-lockers", r"/shipping-label-printer",
    ],
    "Category": [r"/category", r"/collections", r"/catalog", r"/solutions", r"/services"],
    "Industries": [
        r"/industries", r"/finance", r"/government", r"/education",
        r"/higher-education", r"/retail", r"/healthcare", r"/legal", r"/pharmacy",
    ],
    "Cart": [r"/cart", r"/basket"],
    "Checkout": [r"/checkout", r"/payment"],
    "Contact": [r"/contact", r"/demo", r"/quote", r"/request", r"/request-more-information"],
    "ThankYou": [r"thankyou", r"thank-you", r"show=thankyou", r"confirmation"],
    "Support": [r"/support", r"/knowledge", r"/article", r"/search-results"],
    "Content": [r"/blog", r"/insights", r"/resources", r"/article", r"/news", r"/whitepaper", r"/case-study", r"/webinar", r"/podcast"],
    "Newsroom": [r"/newsroom", r"/press"],
    "Company": [r"/our-company", r"/investor", r"/esg", r"/supplier-information"],
    "API": [r"/api", r"/apis", r"/developer"],
    "Careers": [r"/career", r"/jobs"],
    "Region": [r"/region-select", r"^/[a-z]{2}(-[a-z]{2})?\.html$", r"^/[a-z]{2}/[a-z]{2}\.html$"],
}


def is_thank_you_url(url: str) -> bool:
    """Detect conversion / thank-you pages even when only signalled via a
    query string parameter (e.g. '?show=thankyou'), since path-based
    PAGE_TYPE_PATTERNS alone miss these."""
    return bool(re.search(r"thank.?you|show=thankyou|confirmation", url, re.I))


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class NetworkLogEntry:
    """A single martech-relevant network request captured during a journey audit."""
    timestamp: str
    page_url: str
    action_label: str
    vendor: str
    category: str
    platform: str
    request_url: str
    method: str
    request_post_data: str
    response_status: int
    event_name: str
    measurement_id: str
    client_id: str
    parsed_params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SkippedInteraction:
    """An element the journey auditor detected in a category that requires
    special handling. Categorised so analysts can review coverage.

    If --track-skipped-interactions is enabled, entries of type Hidden,
    Auth_Gated and External_Link additionally get a "tracked" attempt, and
    the outcome of that attempt is recorded in tracked / tracked_notes.
    """
    page_url: str
    interaction_type: str  # Destructive | Auth_Gated | Hidden | Region_Selector | External_Link
    label: str
    href: str
    tag: str
    reason: str
    extra: Dict[str, Any] = field(default_factory=dict)
    tracked: bool = False
    tracked_notes: str = ""


@dataclass
class PageAudit:
    url: str
    page_type: str
    title: str = ""
    http_status: int = 0
    load_time_ms: int = 0
    detected_platforms: List[Dict[str, Any]] = field(default_factory=list)
    consent: Dict[str, Any] = field(default_factory=dict)
    datalayer: Dict[str, Any] = field(default_factory=dict)
    forms: Dict[str, Any] = field(default_factory=dict)
    navigation: Dict[str, Any] = field(default_factory=dict)
    ecommerce: Dict[str, Any] = field(default_factory=dict)
    media: Dict[str, Any] = field(default_factory=dict)
    downloads: Dict[str, Any] = field(default_factory=dict)
    performance: Dict[str, Any] = field(default_factory=dict)
    custom_events: List[str] = field(default_factory=list)
    issues: List[Dict[str, Any]] = field(default_factory=list)
    raw_network_count: int = 0


@dataclass
class JourneyEvent:
    url: str
    action_type: str
    action_label: str
    target: str
    success: bool
    ga4_events: List[Dict[str, Any]] = field(default_factory=list)
    adobe_events: List[Dict[str, Any]] = field(default_factory=list)
    datalayer_events: List[Dict[str, Any]] = field(default_factory=list)
    network_requests: List[str] = field(default_factory=list)
    network_logs: List[NetworkLogEntry] = field(default_factory=list)
    notes: str = ""


@dataclass
class WebsiteAudit:
    site_url: str
    generated_at: str
    website_type: str
    pages_audited: int
    overall_score: int
    score_breakdown: Dict[str, Any]
    recommendations: List[Dict[str, str]]
    pages: List[PageAudit]
    journey_events: List[JourneyEvent]
    discovered_urls: List[str]
    network_logs: List[NetworkLogEntry] = field(default_factory=list)
    skipped_interactions: List[SkippedInteraction] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Network log helpers
# ---------------------------------------------------------------------------

def classify_martech_request(url: str, post_data: str = "") -> Optional[Dict[str, str]]:
    """Return vendor/category/platform dict if the URL matches a known martech
    endpoint, otherwise return None."""
    combined = url + " " + (post_data or "")
    for pattern, meta in MARTECH_URL_PATTERNS.items():
        if re.search(pattern, combined, re.I):
            return dict(meta)
    return None


def extract_event_name(url: str, post_data: str = "") -> str:
    """Best-effort extraction of a human-readable event name from a network
    request URL / POST body."""
    if re.search(r"google-analytics\.com/g/collect|analytics\.google\.com/g/collect", url, re.I):
        params = parse_qs(urlparse(url).query)
        return first(params.get("en")) or first(params.get("event")) or "ga4_collect"
    if re.search(r"\.sc\.omtrdc\.net|\.2o7\.net|/b/ss/", url, re.I):
        params = parse_qs(urlparse(url).query)
        link_type = first(params.get("pe")) or ""
        link_name = first(params.get("pev2")) or ""
        page_name = first(params.get("pageName")) or ""
        if link_type == "lnk_o":
            return f"custom_link: {link_name}" if link_name else "custom_link"
        if link_type == "lnk_d":
            return f"download_link: {link_name}" if link_name else "download_link"
        if link_type == "lnk_e":
            return "exit_link"
        if page_name and is_thank_you_url(page_name):
            return f"conversion_page_view: {page_name}"
        return "page_view"
    if re.search(r"api\.segment\.io|api\.segment\.com", url, re.I):
        try:
            body = json.loads(post_data or "{}")
            return body.get("event") or body.get("type") or "segment_call"
        except Exception:
            return "segment_call"
    if re.search(r"facebook\.com/tr", url, re.I):
        params = parse_qs(urlparse(url).query)
        return first(params.get("ev")) or "fb_pixel_event"
    if re.search(r"api\.amplitude\.com", url, re.I):
        try:
            body = json.loads(post_data or "{}")
            events = body.get("events") or []
            if events:
                return events[0].get("event_type") or "amplitude_event"
        except Exception:
            pass
        return "amplitude_event"
    if re.search(r"api\.mixpanel\.com", url, re.I):
        try:
            params = parse_qs(urlparse(url).query)
            data_b64 = first(params.get("data")) or ""
            if data_b64:
                padded = data_b64 + "=" * (-len(data_b64) % 4)
                decoded = json.loads(base64.b64decode(padded).decode("utf-8", errors="ignore"))
                if isinstance(decoded, list) and decoded:
                    return decoded[0].get("event") or "mixpanel_event"
        except Exception:
            pass
        return "mixpanel_event"
    return ""


def extract_measurement_id(url: str) -> str:
    params = parse_qs(urlparse(url).query)
    return first(params.get("tid")) or first(params.get("measurement_id")) or first(params.get("id")) or ""


def extract_client_id(url: str) -> str:
    params = parse_qs(urlparse(url).query)
    return first(params.get("cid")) or first(params.get("client_id")) or ""


def build_network_log_entry(raw: Dict[str, Any], page_url: str, action_label: str, meta: Dict[str, str]) -> NetworkLogEntry:
    """Construct a NetworkLogEntry from a raw captured request dict."""
    url = raw.get("url", "")
    post_data = raw.get("post_data", "") or ""
    try:
        parsed_params: Dict[str, Any] = {k: (v[0] if len(v) == 1 else v) for k, v in parse_qs(urlparse(url).query).items()}
    except Exception:
        parsed_params = {}
    if post_data:
        try:
            body = json.loads(post_data)
            if isinstance(body, dict):
                parsed_params.update(body)
        except Exception:
            if "=" in post_data:
                try:
                    parsed_params.update({k: (v[0] if len(v) == 1 else v) for k, v in parse_qs(post_data).items()})
                except Exception:
                    pass
    return NetworkLogEntry(
        timestamp=raw.get("timestamp", datetime.now().isoformat(timespec="milliseconds")),
        page_url=page_url,
        action_label=action_label,
        vendor=meta.get("vendor", ""),
        category=meta.get("category", ""),
        platform=meta.get("platform", ""),
        request_url=url[:1000],
        method=raw.get("method", "GET"),
        request_post_data=post_data[:2000],
        response_status=raw.get("response_status", 0),
        event_name=extract_event_name(url, post_data),
        measurement_id=extract_measurement_id(url),
        client_id=extract_client_id(url),
        parsed_params=parsed_params,
    )


def filter_martech_logs(raw_requests: List[Dict[str, Any]], page_url: str, action_label: str) -> List[NetworkLogEntry]:
    """Filter a list of raw request dicts and return only martech-relevant NetworkLogEntry objects."""
    entries: List[NetworkLogEntry] = []
    seen_urls: set = set()
    for raw in raw_requests:
        url = raw.get("url", "")
        meta = classify_martech_request(url, raw.get("post_data", "") or "")
        if meta and url not in seen_urls:
            seen_urls.add(url)
            entries.append(build_network_log_entry(raw, page_url, action_label, meta))
    return entries


# ---------------------------------------------------------------------------
# Page fetcher
# ---------------------------------------------------------------------------

class PageFetcher:
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(self, url: str, use_playwright: bool = False, timeout: int = 25) -> None:
        self.url = ensure_scheme(url)
        self.use_playwright = bool(use_playwright and PLAYWRIGHT_AVAILABLE)
        self.timeout = timeout
        self.html = ""
        self.title = ""
        self.status = 0
        self.headers: Dict[str, Any] = {}
        self.load_time_ms = 0
        self.inline_scripts: List[str] = []
        self.external_scripts: List[str] = []
        self.network_requests: List[str] = []
        self.network_payloads: List[Dict[str, Any]] = []

    def fetch(self) -> bool:
        return self._fetch_playwright() if self.use_playwright else self._fetch_static()

    def _collect_from_html(self) -> None:
        soup = BeautifulSoup(self.html, "lxml" if self.html else "html.parser")
        title_tag = soup.find("title")
        self.title = title_tag.get_text(strip=True) if title_tag else self.title
        self.inline_scripts, self.external_scripts = [], []
        for script in soup.find_all("script"):
            src = script.get("src", "")
            if src:
                self.external_scripts.append(src)
            else:
                text = script.string or script.get_text() or ""
                if text.strip():
                    self.inline_scripts.append(text)

    def _fetch_static(self) -> bool:
        try:
            start = time.time()
            response = requests.get(self.url, headers=self.HEADERS, timeout=self.timeout, allow_redirects=True, verify=False)
            self.load_time_ms = int((time.time() - start) * 1000)
            self.status = response.status_code
            self.headers = dict(response.headers)
            self.html = response.text or ""
            self._collect_from_html()
            return True
        except RequestException as exc:
            print(f"Static fetch failed for {self.url}: {exc}")
            return False

    @staticmethod
    def find_chrome_path() -> Optional[str]:
        paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
        return next((path for path in paths if os.path.exists(path)), None)

    def _fetch_playwright(self) -> bool:
        try:
            with sync_playwright() as playwright:
                launch_args: Dict[str, Any] = {
                    "headless": True,
                    "args": ["--ignore-certificate-errors", "--disable-web-security"],
                }
                chrome = self.find_chrome_path()
                if chrome:
                    launch_args["executable_path"] = chrome
                browser = playwright.chromium.launch(**launch_args)
                context = browser.new_context(ignore_https_errors=True, user_agent=self.HEADERS["User-Agent"], viewport={"width": 1440, "height": 1000})
                page = context.new_page()
                page.on("request", lambda request: self.network_requests.append(request.url))
                page.on("response", lambda response: self.network_payloads.append({"url": response.url, "status": response.status}))
                start = time.time()
                try:
                    response = page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=12000)
                    except Exception:
                        page.wait_for_timeout(5000)
                    if response:
                        self.status = response.status
                        self.headers = dict(response.headers)
                except Exception:
                    page.wait_for_timeout(5000)
                self.load_time_ms = int((time.time() - start) * 1000)
                self.title = page.title()
                self.html = page.content()
                self._collect_from_html()
                browser.close()
                return True
        except Exception as exc:
            print(f"Playwright failed for {self.url} ({exc}). Falling back to static fetch.")
            return self._fetch_static()


# ---------------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------------

class WebsiteCrawler:
    def __init__(self, site_url: str, max_pages: int = 0, include_query: bool = False) -> None:
        self.site_url = normalize_url(ensure_scheme(site_url))
        self.domain = urlparse(self.site_url).netloc
        self.max_pages = max_pages
        self.include_query = include_query

    def discover(self) -> List[str]:
        urls = self._discover_sitemaps()
        urls.extend(self._crawl_links(urls[:20] or [self.site_url]))
        unique: List[str] = []
        for url in urls:
            if url not in unique and self._is_valid_internal_url(url):
                unique.append(url)
            if self.max_pages and len(unique) >= self.max_pages:
                break
        return unique

    def _discover_sitemaps(self) -> List[str]:
        discovered: List[str] = []
        queue = [urljoin(self.site_url, "/sitemap.xml"), urljoin(self.site_url, "/sitemap_index.xml")]
        seen = set()
        while queue:
            sitemap = queue.pop(0)
            if sitemap in seen:
                continue
            seen.add(sitemap)
            try:
                response = requests.get(sitemap, headers=PageFetcher.HEADERS, timeout=15, verify=False)
                if response.status_code != 200:
                    continue
                soup = BeautifulSoup(response.text, "xml")
                locs = [loc.get_text(strip=True) for loc in soup.find_all("loc")]
                if not locs:
                    locs = re.findall(r"<loc>(.*?)</loc>", response.text, flags=re.I | re.S)
                for loc in locs:
                    loc = unquote(loc.strip())
                    if loc.lower().split("?", 1)[0].endswith(".xml"):
                        if loc not in seen:
                            queue.append(loc)
                    else:
                        clean = normalize_url(loc, include_query=self.include_query)
                        if self._is_valid_internal_url(clean):
                            discovered.append(clean)
                        if self.max_pages and len(discovered) >= self.max_pages:
                            return list(dict.fromkeys(discovered))
            except Exception:
                continue
        return list(dict.fromkeys(discovered))

    def _crawl_links(self, seeds: List[str]) -> List[str]:
        discovered: List[str] = []
        visited = set()
        queue = list(dict.fromkeys(seeds + [self.site_url]))
        max_visit = self.max_pages if self.max_pages else 10000
        while queue and len(visited) < max_visit:
            url = queue.pop(0)
            if url in visited or not self._is_valid_internal_url(url):
                continue
            visited.add(url)
            discovered.append(url)
            try:
                fetcher = PageFetcher(url, use_playwright=False, timeout=12)
                if not fetcher.fetch():
                    continue
                soup = BeautifulSoup(fetcher.html, "lxml" if fetcher.html else "html.parser")
                for anchor in soup.find_all("a", href=True):
                    href = anchor.get("href", "").strip()
                    if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
                        continue
                    absolute = normalize_url(urljoin(url, href), include_query=self.include_query)
                    if absolute not in visited and absolute not in queue and self._is_valid_internal_url(absolute):
                        queue.append(absolute)
                if self.max_pages and len(discovered) >= self.max_pages:
                    break
            except Exception:
                continue
        return discovered

    def _is_valid_internal_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        if parsed.netloc.replace("www.", "") != self.domain.replace("www.", ""):
            return False
        blocked = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".zip", ".rar", ".7z", ".mp4", ".mp3", ".css", ".js", ".xml", ".json")
        return not parsed.path.lower().endswith(blocked)


# ---------------------------------------------------------------------------
# Page auditor
# ---------------------------------------------------------------------------

class PageAuditor:
    def audit(self, url: str, deep: bool = False) -> PageAudit:
        fetcher = PageFetcher(url, use_playwright=deep)
        if not fetcher.fetch():
            return PageAudit(url=url, page_type=classify_page_type(url), issues=[issue("HIGH", "Fetch", "Could not fetch page", "Validate page availability and crawler access.")])
        soup = BeautifulSoup(fetcher.html, "lxml" if fetcher.html else "html.parser")
        full_text = "\n".join([fetcher.html, *fetcher.inline_scripts, *fetcher.external_scripts, *fetcher.network_requests])
        result = PageAudit(url=url, page_type=classify_page_type(url, fetcher.html), title=fetcher.title, http_status=fetcher.status, load_time_ms=fetcher.load_time_ms, raw_network_count=len(fetcher.network_requests))
        result.detected_platforms = self._detect_platforms(full_text)
        result.consent = self._audit_consent(full_text, soup, fetcher.network_requests)
        result.datalayer = self._audit_datalayer(full_text)
        result.forms = self._audit_forms(full_text, soup)
        result.navigation = self._audit_navigation(full_text, soup)
        result.ecommerce = self._audit_ecommerce(full_text, result.page_type)
        result.media = self._audit_media(full_text, soup)
        result.downloads = self._audit_downloads(soup)
        result.performance = {
            "load_time_ms": fetcher.load_time_ms,
            "script_count": len(fetcher.external_scripts),
            "tag_script_count": sum(1 for script in fetcher.external_scripts if any(term in script.lower() for term in ["tag", "analytics", "facebook", "linkedin", "hubspot", "adobedtm", "omtrdc"])),
        }
        result.custom_events = self._extract_custom_events(full_text)
        result.issues = self._build_issues(result)
        return result

    @staticmethod
    def _detect_platforms(text: str) -> List[Dict[str, Any]]:
        rows = []
        for name, config in PLATFORMS.items():
            signals = []
            for pattern in config["patterns"]:
                match = re.search(pattern, text, re.I)
                if match:
                    signals.append(match.group(0)[:120])
            if signals:
                rows.append({"platform": name, "vendor": config["vendor"], "category": config["category"], "priority": config["priority"], "signals": sorted(set(signals))[:5]})
        return rows

    @staticmethod
    def _audit_consent(text: str, soup: BeautifulSoup, network: List[str]) -> Dict[str, Any]:
        platform = "None detected"
        if re.search(r"cookielaw\.org|OneTrust|OptanonWrapper", text, re.I):
            platform = "OneTrust"
        elif re.search(r"cookiebot\.com|Cookiebot|CookieConsent", text, re.I):
            platform = "Cookiebot"
        elif re.search(r"usercentrics|didomi|cookiehub|axeptio", text, re.I):
            platform = "Other CMP"
        policy = any(re.search(r"cookie|privacy|gdpr|ccpa|consent", anchor.get("href", "") + " " + anchor.get_text(" ", strip=True), re.I) for anchor in soup.find_all("a", href=True))
        consent_mode = bool(re.search(r"ad_storage|analytics_storage|personalization_storage|security_storage|consent\s*['\"],\s*['\"]default", text, re.I))
        return {"platform": platform, "policy_link_detected": policy, "consent_mode_signal_detected": consent_mode, "potential_prefire_risk": bool(network) and platform == "None detected"}

    @staticmethod
    def _audit_datalayer(text: str) -> Dict[str, Any]:
        detected = bool(re.search(r"window\.dataLayer|var\s+dataLayer|dataLayer\s*=", text, re.I))
        pushes = re.findall(r"dataLayer\.push\s*\(\s*\{(.*?)\}\s*\)", text, re.I | re.S)
        variables = set()
        for push in pushes[:50]:
            variables.update(re.findall(r"['\"]([^'\"]+)['\"]\s*:", push))
        return {"detected": detected, "push_count": len(pushes), "variables": sorted(variables)}

    @staticmethod
    def _audit_forms(text: str, soup: BeautifulSoup) -> Dict[str, Any]:
        count = len(soup.find_all("form"))
        signals = sorted(set(re.findall(r"form_submit|form_start|generate_lead|contact_us|formComplete|formSubmit", text, re.I)))
        return {"forms_found": count, "form_tracking_signals": signals, "has_gap": bool(count and not signals)}

    @staticmethod
    def _audit_navigation(text: str, soup: BeautifulSoup) -> Dict[str, Any]:
        nav_count = len(soup.find_all("nav"))
        signals = sorted(set(re.findall(r"navigation|menu_click|nav_click|menu_open|header_click|footer_click|cta_click|button_click", text, re.I)))
        ctas = []
        for anchor in soup.find_all("a", href=True):
            label, href = anchor.get_text(" ", strip=True), anchor.get("href", "")
            if re.search(r"contact|demo|quote|buy|shop|learn more|download|get started|request|subscribe", label + " " + href, re.I):
                ctas.append({"label": label[:80], "href": href[:160]})
        return {"nav_elements": nav_count, "tracking_signals": signals, "cta_candidates": ctas[:25], "has_gap": bool((nav_count or ctas) and not signals)}

    @staticmethod
    def _audit_ecommerce(text: str, page_type: str) -> Dict[str, Any]:
        applicable = bool(re.search(r"add[_ -]?to[_ -]?cart|begin_checkout|view_cart|shopify|woocommerce|magento|bigcommerce|/cart|/checkout", text, re.I)) or page_type in ("Cart", "Checkout")
        expected = ["view_item", "select_item", "add_to_cart", "view_cart", "begin_checkout", "add_shipping_info", "add_payment_info", "purchase"]
        found = [event for event in expected if re.search(rf"['\"]{event}['\"]", text, re.I)]
        return {"ecommerce_applicable": applicable, "ga4_ecommerce_events_found": found, "missing_if_applicable": [event for event in expected if event not in found] if applicable else []}

    @staticmethod
    def _audit_media(text: str, soup: BeautifulSoup) -> Dict[str, Any]:
        videos = len(soup.find_all("video")) + len(soup.find_all("iframe", src=re.compile(r"youtube|vimeo|wistia", re.I)))
        audios = len(soup.find_all("audio"))
        return {"videos_found": videos, "audio_found": audios, "video_tracking_signals": sorted(set(re.findall(r"video_start|video_progress|video_complete|video_play|video_pause", text, re.I))), "audio_tracking_signals": sorted(set(re.findall(r"audio_play|audio_progress|audio_complete|podcast_play", text, re.I)))}

    @staticmethod
    def _audit_downloads(soup: BeautifulSoup) -> Dict[str, Any]:
        links = []
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href", "")
            if re.search(r"\.(pdf|doc|docx|xls|xlsx|ppt|pptx|zip)(\?|$)", href, re.I):
                links.append({"label": anchor.get_text(" ", strip=True)[:80], "href": href[:180]})
        return {"download_links_found": len(links), "examples": links[:25]}

    @staticmethod
    def _extract_custom_events(text: str) -> List[str]:
        patterns = [
            r"gtag\(['\"]event['\"],\s*['\"]([^'\"]+)['\"]",
            r"dataLayer\.push\s*\(\s*\{\s*['\"]event['\"]\s*:\s*['\"]([^'\"]+)['\"]",
            r"analytics\.track\(['\"]([^'\"]+)['\"]",
            r"fbq\(['\"]track(?:Custom)?['\"],\s*['\"]([^'\"]+)['\"]",
        ]
        events = []
        for pattern in patterns:
            events.extend(re.findall(pattern, text, re.I | re.S))
        return sorted(set(events))

    @staticmethod
    def _build_issues(page: PageAudit) -> List[Dict[str, Any]]:
        issues = []
        if page.http_status >= 400 or page.http_status == 0:
            issues.append(issue("HIGH", "Availability", f"Page returned HTTP {page.http_status}", "Validate page access and crawler permissions."))
        if page.consent.get("platform") == "None detected":
            issues.append(issue("CRITICAL", "Consent", "No consent management platform detected", "Validate CMP implementation and consent gating."))
        if page.datalayer.get("detected") and page.datalayer.get("push_count") == 0:
            issues.append(issue("HIGH", "DataLayer", "dataLayer exists but no static dataLayer.push events were found", "Validate runtime dataLayer events using the journey audit."))
        if page.forms.get("has_gap"):
            issues.append(issue("HIGH", "Forms", "Forms detected but no form tracking signal found", "Implement form_start and form_submit or generate_lead tracking."))
        if page.navigation.get("has_gap"):
            issues.append(issue("MEDIUM", "Navigation", "Navigation or CTA elements found but no click tracking signal detected", "Implement nav_click, cta_click, header_click and footer_click events."))
        if page.ecommerce.get("ecommerce_applicable") and not page.ecommerce.get("ga4_ecommerce_events_found"):
            issues.append(issue("HIGH", "Ecommerce", "Ecommerce journey detected but no GA4 ecommerce events found", "Implement or validate the GA4 ecommerce event schema."))
        if page.performance.get("load_time_ms", 0) > 5000:
            issues.append(issue("MEDIUM", "Performance", f"Page load time above 5 seconds: {page.performance.get('load_time_ms')} ms", "Review third-party script impact."))
        return issues


# ---------------------------------------------------------------------------
# Journey auditor
# ---------------------------------------------------------------------------

class JourneyAuditor:
    def __init__(
        self,
        submit_forms: bool = False,
        max_clicks_per_page: int = 8,
        scroll_steps: Optional[List[int]] = None,
        region: str = "",
        track_skipped_interactions: bool = False,
        max_tracked_per_type: int = 5,
    ) -> None:
        self.submit_forms = submit_forms
        self.max_clicks_per_page = max_clicks_per_page
        self.scroll_steps = scroll_steps or [25, 50, 75, 100]
        # Target region/country/language to attempt selecting when a region
        # selector is encountered. Empty string means "log but do not interact".
        self.region = region.strip()
        # Whether Hidden / Auth_Gated / External_Link items should also be
        # safely clicked (tracked) rather than only logged.
        self.track_skipped_interactions = track_skipped_interactions
        # Cap per category per page to keep runtime bounded.
        self.max_tracked_per_type = max_tracked_per_type

    def audit_page(self, url: str) -> Tuple[List[JourneyEvent], List[SkippedInteraction]]:
        if not PLAYWRIGHT_AVAILABLE:
            return (
                [JourneyEvent(url=url, action_type="Journey", action_label="playwright_unavailable", target=url, success=False, notes="Install Playwright: pip install playwright; playwright install chromium")],
                [],
            )
        events: List[JourneyEvent] = []
        skipped: List[SkippedInteraction] = []
        raw_network: List[Dict[str, Any]] = []

        def _on_request(request: Any) -> None:
            try:
                post_data = ""
                try:
                    post_data = request.post_data or ""
                except Exception:
                    pass
                raw_network.append({
                    "timestamp": datetime.now().isoformat(timespec="milliseconds"),
                    "url": request.url,
                    "method": request.method,
                    "post_data": post_data,
                    "response_status": 0,
                })
            except Exception:
                pass

        def _on_response(response: Any) -> None:
            try:
                resp_url, status = response.url, response.status
                for i in range(len(raw_network) - 1, -1, -1):
                    entry = raw_network[i]
                    if entry["url"] == resp_url and entry["response_status"] == 0:
                        entry["response_status"] = status
                        break
            except Exception:
                pass

        try:
            with sync_playwright() as playwright:
                launch_args: Dict[str, Any] = {
                    "headless": True,
                    "args": ["--ignore-certificate-errors", "--disable-web-security", "--disable-features=IsolateOrigins,site-per-process"],
                }
                chrome = PageFetcher.find_chrome_path()
                if chrome:
                    launch_args["executable_path"] = chrome
                browser = playwright.chromium.launch(**launch_args)
                context = browser.new_context(ignore_https_errors=True, user_agent=PageFetcher.HEADERS["User-Agent"], viewport={"width": 1440, "height": 1000})
                page = context.new_page()
                page.on("request", _on_request)
                page.on("response", _on_response)

                # ---- initial page load ----
                initial_before = len(raw_network)
                self._navigate(page, url)
                events.append(self._make_event(page, url, "Page Load", "initial_load_pre_consent", url, True, raw_network[initial_before:]))

                # ---- consent banner ----
                consent_before = len(raw_network)
                if self._handle_cookie_banner(page):
                    events.append(self._make_event(page, url, "Consent", "cookie_accept", "CMP", True, raw_network[consent_before:]))
                page.wait_for_timeout(3000)

                # ---- detect all skipped interaction categories ----
                page_skipped = self._detect_skipped_interactions(page, url)
                skipped.extend(page_skipped)

                # ---- handle region selector (if --region was specified) ----
                region_entries = [s for s in page_skipped if s.interaction_type == "Region_Selector"]
                if region_entries and self.region:
                    region_before = len(raw_network)
                    region_result = self._handle_region_selector(page, url, self.region)
                    if region_result:
                        events.append(self._make_event(page, url, "Region_Select", f"region_selected: {self.region}", "region_selector", True, raw_network[region_before:]))
                        page.wait_for_timeout(3000)
                elif region_entries and not self.region:
                    print(
                        f"  [REGION] {len(region_entries)} region selector(s) detected on {url}.\n"
                        f"  Re-run with --region \"<country/language>\" to target localised content.\n"
                        f"  Available options: {region_entries[0].extra.get('options', [])[:10]}"
                    )

                # ---- safely track Hidden / Auth_Gated / External_Link ----
                if self.track_skipped_interactions:
                    tracked_events = self._track_flagged_interactions(page, url, page_skipped, raw_network)
                    events.extend(tracked_events)
                    if page.url != url:
                        self._navigate(page, url)
                        self._handle_cookie_banner(page)

                # ---- scroll steps ----
                for percentage in self.scroll_steps:
                    before = len(raw_network)
                    try:
                        page.evaluate(
                            "pct => window.scrollTo(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight) * pct / 100)",
                            percentage,
                        )
                        page.wait_for_timeout(2500)
                        events.append(self._make_event(page, url, "Scroll", f"scroll_{percentage}_percent", url, True, raw_network[before:]))
                    except Exception as exc:
                        events.append(JourneyEvent(url=url, action_type="Scroll", action_label=f"scroll_{percentage}_percent_failed", target=url, success=False, notes=str(exc)[:250]))

                # ---- click interactions (safe/visible CTAs) ----
                clicked = 0
                attempted_keys: set = set()
                while clicked < self.max_clicks_per_page:
                    candidates = self._get_click_candidates(page)
                    candidate = next((item for item in candidates if (item.get("label", ""), item.get("href", "")) not in attempted_keys), None)
                    if not candidate:
                        break
                    label = candidate.get("label", "click")[:120]
                    selector = candidate.get("selector", "")
                    href = candidate.get("href", "")
                    attempted_keys.add((label, href))
                    if not selector or is_risky_click(label, href):
                        continue
                    locator = page.locator(selector).first
                    try:
                        if locator.count() == 0 or not locator.is_visible(timeout=1000) or not locator.is_enabled(timeout=1000):
                            continue
                    except Exception:
                        continue
                    before = len(raw_network)
                    starting_url = page.url
                    try:
                        locator.scroll_into_view_if_needed(timeout=2500)
                        page.wait_for_timeout(250)
                        if not locator.is_visible(timeout=1000):
                            continue
                        print(f"CLICKING -> LABEL={label} TARGET={href or selector}")
                        locator.click(timeout=5000)
                        page.wait_for_timeout(4000)
                        # NOTE: _make_event now records page.url (the actual
                        # destination reached after the click, e.g. a
                        # "?show=thankyou" conversion page) rather than the
                        # source journey `url`, with the source page kept in
                        # the event's target/notes for traceability.
                        events.append(self._make_event(page, url, "Click", label, href or selector, True, raw_network[before:]))
                        clicked += 1
                    except Exception as exc:
                        events.append(JourneyEvent(url=url, action_type="Click", action_label=f"click_failed: {label}", target=href or selector, success=False, notes=str(exc)[:300]))
                    finally:
                        if page.is_closed():
                            break
                        if page.url != starting_url:
                            self._navigate(page, starting_url)
                            self._handle_cookie_banner(page)

                # ---- form audit ----
                if not page.is_closed():
                    self._audit_forms(page, url, raw_network, events)

                browser.close()

        except Exception as exc:
            events.append(JourneyEvent(url=url, action_type="Journey", action_label="audit_failed", target=url, success=False, notes=str(exc)[:500]))
        return events, skipped

    @staticmethod
    def _navigate(page: Any, url: str) -> None:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                page.wait_for_timeout(5000)
        except Exception:
            page.wait_for_timeout(5000)

    @staticmethod
    def _handle_cookie_banner(page: Any) -> bool:
        selectors = [
            "#onetrust-accept-btn-handler", "#accept-recommended-btn-handler", "#securiti-consent-accept",
            "button:has-text('I Accept')", "button:has-text('Accept All')", "button:has-text('Accept all')",
            "button:has-text('Allow all')", "button:has-text('Agree')",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible(timeout=1000):
                    locator.click(timeout=5000)
                    page.wait_for_timeout(3000)
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _try_open_mobile_menu(page: Any) -> bool:
        """Attempt to reveal off-canvas/hamburger navigation so Hidden items
        that live inside a collapsed menu become clickable. Returns True if a
        menu toggle was found and clicked."""
        selectors = [
            "button:has-text('Main menu')",
            "button:has-text('Menu')",
            "button:has-text('Expand menu')",
            "[aria-label*='menu' i]",
            "button[aria-expanded='false']",
            ".hamburger", ".menu-toggle", ".nav-toggle",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible(timeout=800):
                    locator.click(timeout=2000)
                    page.wait_for_timeout(1200)
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _close_mobile_menu(page: Any) -> None:
        """Best-effort close of any opened mobile menu / overlay so subsequent
        interactions aren't blocked by an open panel."""
        selectors = [
            "button:has-text('Close')", "[aria-label*='close' i]",
            "button[aria-expanded='true']",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible(timeout=500):
                    locator.click(timeout=1500)
                    page.wait_for_timeout(500)
                    return
            except Exception:
                continue

    @staticmethod
    def _css_escape(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "")
            .replace("\r", "")
        )

    def _find_special_locator(self, page: Any, interaction: "SkippedInteraction") -> Any:
        """Best-effort locator lookup for a previously-detected skipped
        interaction, matched by href first (more reliable), falling back to
        exact label text."""
        if interaction.href:
            escaped_href = self._css_escape(interaction.href)
            try:
                locator = page.locator(f'a[href="{escaped_href}"]').first
                if locator.count() > 0:
                    return locator
            except Exception:
                pass
        if interaction.label:
            try:
                return page.get_by_text(interaction.label, exact=True).first
            except Exception:
                pass
        return page.locator("a[href='__no_match__']")

    def _track_flagged_interactions(
        self,
        page: Any,
        url: str,
        skipped: List[SkippedInteraction],
        raw_network: List[Dict[str, Any]],
    ) -> List[JourneyEvent]:
        """Safely 'track' three categories of previously-skipped interactions,
        without performing anything irreversible:

        - Hidden: reveal via the mobile/hamburger menu toggle, then click the
          now-visible element. Purely navigational - no data submitted.
        - Auth_Gated: click through far enough to observe the analytics hit
          fired on click (and/or on landing on the login page), then return
          to the original page WITHOUT entering credentials.
        - External_Link: click through far enough to observe the outbound
          click-tracking beacon, then return to the original page WITHOUT
          interacting with the external site.

        Destructive items are never tracked, even with this flag enabled.
        """
        tracked_events: List[JourneyEvent] = []
        original_url = page.url
        trackable_types = ("Hidden", "Auth_Gated", "External_Link")

        for interaction_type in trackable_types:
            items = [s for s in skipped if s.interaction_type == interaction_type and (s.href or s.label)][: self.max_tracked_per_type]
            if not items:
                continue

            if interaction_type == "Hidden":
                self._try_open_mobile_menu(page)
                page.wait_for_timeout(500)

            for item in items:
                before = len(raw_network)
                try:
                    locator = self._find_special_locator(page, item)
                    if locator.count() == 0:
                        item.tracked, item.tracked_notes = False, "Element not found for tracking attempt."
                        continue
                    if not locator.is_visible(timeout=1200):
                        item.tracked, item.tracked_notes = False, "Element still not visible (menu reveal did not expose it)."
                        continue
                    locator.scroll_into_view_if_needed(timeout=1500)
                    print(f"TRACKING -> TYPE={interaction_type} LABEL={item.label} TARGET={item.href}")
                    locator.click(timeout=5000)
                    page.wait_for_timeout(2500)
                    tracked_events.append(self._make_event(page, url, f"{interaction_type}_Tracked", item.label, item.href, True, raw_network[before:]))
                    item.tracked = True
                    if interaction_type == "Auth_Gated":
                        item.tracked_notes = "Clicked through to auth wall; no credentials entered; returned to original page."
                    elif interaction_type == "External_Link":
                        item.tracked_notes = "Clicked through far enough to capture outbound click beacon; did not interact with external site."
                    else:
                        item.tracked_notes = "Revealed via menu toggle and clicked successfully."
                except Exception as exc:
                    item.tracked, item.tracked_notes = False, f"Click failed: {str(exc)[:200]}"
                    tracked_events.append(JourneyEvent(url=url, action_type=f"{interaction_type}_Tracked", action_label=f"tracking_failed: {item.label}", target=item.href or item.label, success=False, notes=str(exc)[:250]))
                finally:
                    if not page.is_closed() and page.url != original_url:
                        self._navigate(page, original_url)
                        self._handle_cookie_banner(page)
                        if interaction_type == "Hidden":
                            self._try_open_mobile_menu(page)
                            page.wait_for_timeout(500)

            if interaction_type == "Hidden":
                self._close_mobile_menu(page)
                if page.url != original_url:
                    self._navigate(page, original_url)
                    self._handle_cookie_banner(page)

        return tracked_events

    @staticmethod
    def _get_click_candidates(page: Any) -> List[Dict[str, str]]:
        script = r"""
        () => {
          const getRootDomain = host => {
            const parts = host.toLowerCase().split('.').filter(Boolean);
            return parts.length > 2 ? parts.slice(-2).join('.') : host.toLowerCase();
          };
          const rootDomain = getRootDomain(window.location.hostname);
          const hardBlockRx = /log.?out|sign.?out|delete|remove|unsubscribe|cancel|clear.?cart|checkout|payment|pay.?now|place.?order|submit.?order|confirm.?purchase/i;
          const authGatedRx = /sign.?in|log.?in|register|create.?account|order.?history|view.?order|pay.?bill|my.?account|reorder/i;
          const regionRx = /region|country|language|location.?selector|change.?location|select.?location/i;
          const priorityRx = /contact|demo|quote|request|download|get.?started|learn.?more|talk|sales|subscribe/i;
          const nodes = Array.from(document.querySelectorAll('a[href], button, input[type="button"]'));
          const candidates = [];
          let id = 0;
          for (const el of nodes) {
            if (id >= 200) break;
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            const visible = rect.width >= 20 && rect.height >= 16 && style.visibility !== 'hidden' && style.display !== 'none' && Number(style.opacity || 1) > 0 && el.getAttribute('aria-hidden') !== 'true' && el.offsetParent !== null;
            if (!visible || el.disabled) continue;
            const href = (el.getAttribute('href') || '').trim();
            const directText = Array.from(el.childNodes).filter(node => node.nodeType === Node.TEXT_NODE).map(node => (node.textContent || '').trim()).join(' ').trim();
            const label = (directText || el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('value') || '').replace(/\s+/g, ' ').trim().slice(0, 100);
            if (!label || label.toLowerCase() === 'click') continue;
            if (hardBlockRx.test(label + ' ' + href) || authGatedRx.test(label + ' ' + href) || regionRx.test(label + ' ' + href)) continue;
            if (href.startsWith('mailto:') || href.startsWith('tel:') || href.startsWith('javascript:') || href === '#') continue;
            if (href.startsWith('http')) {
              try {
                if (getRootDomain(new URL(href).hostname) !== rootDomain) continue;
              } catch (error) {
                continue;
              }
            }
            el.setAttribute('data-martech-audit-click-id', String(id));
            candidates.push({selector: `[data-martech-audit-click-id="${id}"]`, label, href, priority: priorityRx.test(label + ' ' + href) ? 0 : 1});
            id += 1;
          }
          return candidates.sort((a, b) => a.priority - b.priority).slice(0, 40);
        }
        """
        try:
            return page.evaluate(script) or []
        except Exception:
            return []

    @staticmethod
    def _detect_skipped_interactions(page: Any, page_url: str) -> List[SkippedInteraction]:
        """Scan the live DOM for elements the click loop intentionally skips and
        return a structured log of what was found and why.

        Auth-gated detection is intentionally narrow (sign in / log in /
        register / order history / pay bill / my account / reorder) so that
        legitimate public support links such as "Returns", "Your Account
        Support" and "Rewards" are treated as normal navigation rather than
        being incorrectly flagged as requiring authentication.
        """
        script = r"""
        () => {
          const getRootDomain = host => {
            const parts = host.toLowerCase().split('.').filter(Boolean);
            return parts.length > 2 ? parts.slice(-2).join('.') : host.toLowerCase();
          };
          const rootDomain = getRootDomain(window.location.hostname);

          const destructiveRx = /log.?out|sign.?out|delete|remove|unsubscribe|clear.?cart|checkout|pay.?now|place.?order|submit.?order|confirm.?purchase/i;
          const authGatedRx = /sign.?in|log.?in|register|create.?account|order.?history|view.?order|pay.?bill|my.?account|reorder/i;
          const regionRx = /\b(region|country|language|locale|location.?selector|change.?country|select.?country|select.?region|change.?language|change.?location)\b/i;

          const hiddenReasons = (el, rect, style) => {
            const reasons = [];
            if (rect.width < 1 || rect.height < 1) reasons.push('zero_size');
            if (style.display === 'none') reasons.push('display_none');
            if (style.visibility === 'hidden') reasons.push('visibility_hidden');
            if (Number(style.opacity || 1) === 0) reasons.push('opacity_zero');
            if (el.getAttribute('aria-hidden') === 'true') reasons.push('aria_hidden');
            if (el.offsetParent === null && style.position !== 'fixed') reasons.push('no_offset_parent');
            return reasons;
          };

          const results = { destructive: [], auth_gated: [], hidden: [], region_selectors: [], external: [] };
          const seen = new Set();

          const nodes = Array.from(document.querySelectorAll('a[href], button, input[type="button"], select, [role="button"]'));

          for (const el of nodes) {
            if (seen.size >= 500) break;

            const href = (el.getAttribute('href') || '').trim();
            const rawText = (el.textContent || '').replace(/\s+/g, ' ').trim();
            const label = (rawText || el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('value') || '').slice(0, 120);
            if (!label) continue;

            const key = label + '|' + href;
            if (seen.has(key)) continue;
            seen.add(key);

            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            const tag = el.tagName.toLowerCase();
            const combined = label + ' ' + href;

            const hiddenR = hiddenReasons(el, rect, style);
            if (hiddenR.length > 0) {
              if (results.hidden.length < 80) {
                results.hidden.push({ label, href, tag, hidden_reason: hiddenR.join(', ') });
              }
              continue;
            }

            if (destructiveRx.test(combined)) {
              results.destructive.push({ label, href, tag, reason: 'destructive_action' });
              continue;
            }

            if (authGatedRx.test(combined)) {
              results.auth_gated.push({ label, href, tag, reason: 'auth_gated' });
              continue;
            }

            if (regionRx.test(combined) || tag === 'select') {
              const isRegionSelect = regionRx.test(
                (el.getAttribute('name') || '') + ' ' + (el.getAttribute('id') || '') + ' ' + combined
              );
              if (isRegionSelect || (tag === 'select' && regionRx.test((el.getAttribute('name') || '') + ' ' + (el.getAttribute('id') || '')))) {
                let options = [];
                if (tag === 'select') {
                  options = Array.from(el.options).map(o => o.text.trim()).filter(Boolean).slice(0, 30);
                }
                const selectName = el.getAttribute('name') || el.getAttribute('id') || '';
                results.region_selectors.push({ label, href, tag, options, select_name: selectName });
                continue;
              }
            }

            if (href.startsWith('http')) {
              try {
                const extDomain = new URL(href).hostname;
                if (getRootDomain(extDomain) !== rootDomain) {
                  if (results.external.length < 100) {
                    results.external.push({ label, href, tag, external_domain: extDomain });
                  }
                }
              } catch (e) {}
            }
          }

          return results;
        }
        """
        try:
            raw: Dict[str, List[Dict[str, Any]]] = page.evaluate(script) or {}
        except Exception as exc:
            print(f"  [WARN] Skipped-interaction detection failed: {exc}")
            return []

        entries: List[SkippedInteraction] = []

        for item in raw.get("destructive", []):
            entries.append(SkippedInteraction(
                page_url=page_url, interaction_type="Destructive",
                label=item.get("label", ""), href=item.get("href", ""), tag=item.get("tag", ""),
                reason="Matches destructive-action pattern - clicking could irreversibly alter data or trigger a purchase/deletion. Never tracked, even with --track-skipped-interactions.",
                extra={},
            ))

        for item in raw.get("auth_gated", []):
            entries.append(SkippedInteraction(
                page_url=page_url, interaction_type="Auth_Gated",
                label=item.get("label", ""), href=item.get("href", ""), tag=item.get("tag", ""),
                reason="Requires authentication - clicking would redirect to a login/register wall. With --track-skipped-interactions, the click is followed far enough to capture the analytics beacon, then the page returns without submitting credentials.",
                extra={},
            ))

        for item in raw.get("hidden", []):
            entries.append(SkippedInteraction(
                page_url=page_url, interaction_type="Hidden",
                label=item.get("label", ""), href=item.get("href", ""), tag=item.get("tag", ""),
                reason=f"Element is not interactable: {item.get('hidden_reason', '')}. With --track-skipped-interactions, the mobile/hamburger menu is opened first to try to reveal and click it.",
                extra={"hidden_reason": item.get("hidden_reason", "")},
            ))

        for item in raw.get("region_selectors", []):
            opts = item.get("options", [])
            entries.append(SkippedInteraction(
                page_url=page_url, interaction_type="Region_Selector",
                label=item.get("label", ""), href=item.get("href", ""), tag=item.get("tag", ""),
                reason="Region / country / language selector detected. Specify --region to auto-select a locale, or choose one manually to audit localised analytics.",
                extra={"options": opts, "select_name": item.get("select_name", ""), "available_count": len(opts)},
            ))

        for item in raw.get("external", []):
            entries.append(SkippedInteraction(
                page_url=page_url, interaction_type="External_Link",
                label=item.get("label", ""), href=item.get("href", ""), tag=item.get("tag", ""),
                reason="Link points to an external domain. With --track-skipped-interactions, the click is followed far enough to capture the outbound click-tracking beacon, then the page returns without interacting with the external site.",
                extra={"external_domain": item.get("external_domain", "")},
            ))

        return entries

    def _handle_region_selector(self, page: Any, url: str, region: str) -> bool:
        """Attempt to select ``region`` inside any visible region/country/language
        select element or clickable region-labelled element on the page."""
        script = r"""
        (regionValue) => {
          const regionRx = /region|country|language|locale|location/i;
          const selects = Array.from(document.querySelectorAll('select')).filter(el => {
            const attr = (el.getAttribute('name') || '') + ' ' + (el.getAttribute('id') || '');
            return regionRx.test(attr);
          });
          for (const sel of selects) {
            const options = Array.from(sel.options);
            const match = options.find(o =>
              o.text.toLowerCase().includes(regionValue.toLowerCase()) ||
              o.value.toLowerCase().includes(regionValue.toLowerCase())
            );
            if (match) {
              sel.value = match.value;
              sel.dispatchEvent(new Event('change', { bubbles: true }));
              return { found: true, selected: match.text, element: 'select' };
            }
          }
          const allEls = Array.from(document.querySelectorAll('a, button, [role="option"], li'));
          for (const el of allEls) {
            const label = (el.textContent || '').trim();
            if (label.toLowerCase().includes(regionValue.toLowerCase())) {
              el.click();
              return { found: true, selected: label, element: el.tagName.toLowerCase() };
            }
          }
          return { found: false };
        }
        """
        try:
            result = page.evaluate(script, region)
            if result and result.get("found"):
                print(f"  [REGION] Selected '{result.get('selected')}' via {result.get('element')} on {url}")
                page.wait_for_timeout(3000)
                return True
            else:
                print(f"  [REGION] Could not find option matching '{region}' on {url}. Check --region value against the available options in Skipped_Interactions tab.")
                return False
        except Exception as exc:
            print(f"  [REGION] Region selection error on {url}: {exc}")
            return False

    def _audit_forms(self, page: Any, url: str, raw_network: List[Dict[str, Any]], events: List[JourneyEvent]) -> None:
        forms = page.locator("form:visible")
        for index in range(min(forms.count(), 5)):
            try:
                form = forms.nth(index)
                before = len(raw_network)
                inputs = form.locator("input:visible, textarea:visible, select:visible")
                for input_index in range(min(inputs.count(), 12)):
                    element = inputs.nth(input_index)
                    name = element.get_attribute("name") or element.get_attribute("id") or ""
                    input_type = (element.get_attribute("type") or "text").lower()
                    if input_type in ("hidden", "submit", "button", "checkbox", "radio", "file"):
                        continue
                    try:
                        if element.evaluate("el => el.tagName.toLowerCase()") == "select":
                            continue
                        element.fill(sample_value_for_field(name, input_type), timeout=1500)
                    except Exception:
                        pass
                label = f"form_{index + 1}_filled_dry_run_not_submitted"
                if self.submit_forms:
                    submit = form.locator("button[type='submit']:visible, input[type='submit']:visible").first
                    if submit.count() and submit.is_visible():
                        submit.click(timeout=4000)
                        page.wait_for_timeout(4000)
                        label = f"form_{index + 1}_submitted"
                events.append(self._make_event(page, url, "Form", label, f"form_{index + 1}", True, raw_network[before:]))
            except Exception as exc:
                events.append(JourneyEvent(url=url, action_type="Form", action_label=f"form_{index + 1}_failed", target=f"form_{index + 1}", success=False, notes=str(exc)[:250]))

    @staticmethod
    def _make_event(page: Any, url: str, action_type: str, label: str, target: str, success: bool, new_raw_network: List[Dict[str, Any]]) -> JourneyEvent:
        """Build a JourneyEvent from captured raw network entries.

        IMPORTANT FIX: `url` here is now attributed to the DESTINATION page
        (page.url after the click has resolved), not the originating journey
        page. This ensures that conversion / thank-you pages reached via a
        click (e.g. "...request-more-information.html?show=thankyou") show up
        as their own row in Journey_Audit / Network_Logs, matching what the
        network capture already correctly observed. The originating journey
        page is preserved in `notes` for traceability.
        """
        destination_url = url
        try:
            if hasattr(page, "url") and page.url:
                destination_url = page.url
        except Exception:
            pass

        new_urls = [r.get("url", "") for r in new_raw_network]
        ga4 = [parse_ga4_request(r) for r in new_urls if is_ga4_request(r)]
        adobe = [parse_adobe_request(r) for r in new_urls if is_adobe_request(r)]
        try:
            datalayer = page.evaluate("() => Array.isArray(window.dataLayer) ? window.dataLayer.slice(-30) : []") or []
        except Exception:
            datalayer = []
        network_logs = filter_martech_logs(new_raw_network, destination_url, label)

        notes = ""
        if destination_url != url:
            notes = f"Source Page: {url}"
        if is_thank_you_url(destination_url):
            notes = (notes + " | " if notes else "") + "Conversion/Thank-You page detected."

        return JourneyEvent(
            url=destination_url, action_type=action_type, action_label=label, target=target, success=success,
            ga4_events=ga4, adobe_events=adobe, datalayer_events=datalayer,
            network_requests=new_urls[:80], network_logs=network_logs, notes=notes,
        )


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

class ReportBuilder:
    def build_website_audit(
        self,
        site_url: str,
        pages: List[PageAudit],
        journeys: List[JourneyEvent],
        discovered_urls: List[str],
        skipped_interactions: Optional[List[SkippedInteraction]] = None,
    ) -> WebsiteAudit:
        website_type = classify_website_type(pages)
        score, breakdown = compute_overall_score(pages, journeys, website_type)
        all_network_logs: List[NetworkLogEntry] = []
        for event in journeys:
            all_network_logs.extend(event.network_logs)
        return WebsiteAudit(
            site_url=site_url,
            generated_at=datetime.now().isoformat(timespec="seconds"),
            website_type=website_type,
            pages_audited=len(pages),
            overall_score=score,
            score_breakdown=breakdown,
            recommendations=generate_overall_recommendations(pages, journeys),
            pages=pages,
            journey_events=journeys,
            discovered_urls=discovered_urls,
            network_logs=all_network_logs,
            skipped_interactions=skipped_interactions or [],
        )

    def export(self, audit: WebsiteAudit, output_dir: str = DEFAULT_OUTPUT_DIR) -> Tuple[str, str]:
        os.makedirs(output_dir, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9_]+", "_", urlparse(audit.site_url).netloc or "site").strip("_") or "site"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = os.path.join(output_dir, f"enterprise_martech_audit_{slug}_{timestamp}.json")
        excel_path = os.path.join(output_dir, f"enterprise_martech_audit_{slug}_{timestamp}.xlsx")

        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(asdict(audit), handle, indent=2, ensure_ascii=False, default=str)

        page_rows, platform_rows, issue_rows = [], [], []
        journey_rows, ga4_rows, adobe_rows, datalayer_rows = [], [], [], []
        network_log_rows: List[Dict[str, Any]] = []

        for page in audit.pages:
            page_rows.append({
                "URL": page.url, "Page Type": page.page_type, "Title": page.title,
                "HTTP Status": page.http_status, "Load Time MS": page.load_time_ms,
                "Platforms Found": ", ".join(item["platform"] for item in page.detected_platforms),
                "Consent Platform": page.consent.get("platform"),
                "Consent Mode Signal": page.consent.get("consent_mode_signal_detected"),
                "dataLayer Detected": page.datalayer.get("detected"),
                "dataLayer Push Count": page.datalayer.get("push_count"),
                "Forms Found": page.forms.get("forms_found"),
                "Form Tracking Signals": ", ".join(page.forms.get("form_tracking_signals", [])),
                "CTA Candidates": len(page.navigation.get("cta_candidates", [])),
                "Navigation Tracking Signals": ", ".join(page.navigation.get("tracking_signals", [])),
                "Ecommerce Applicable": page.ecommerce.get("ecommerce_applicable"),
                "GA4 Ecommerce Events Found": ", ".join(page.ecommerce.get("ga4_ecommerce_events_found", [])),
                "Videos Found": page.media.get("videos_found"),
                "Downloads Found": page.downloads.get("download_links_found"),
                "Issue Count": len(page.issues),
            })
            for platform in page.detected_platforms:
                platform_rows.append({"URL": page.url, **platform, "signals": "; ".join(platform.get("signals", []))})
            for item in page.issues:
                issue_rows.append({"URL": page.url, **item})

        for event in audit.journey_events:
            journey_rows.append({
                "URL": event.url, "Action Type": event.action_type,
                "Action Label": event.action_label, "Target": event.target,
                "Success": event.success, "GA4 Hit Count": len(event.ga4_events),
                "Adobe Hit Count": len(event.adobe_events),
                "DataLayer Recent Count": len(event.datalayer_events),
                "Network Log Count": len(event.network_logs),
                "Total Network Count": len(event.network_requests),
                "Notes": event.notes,
            })
            for item in event.ga4_events:
                ga4_rows.append({"URL": event.url, "Action": event.action_label, **item})
            for item in event.adobe_events:
                adobe_rows.append({"URL": event.url, "Action": event.action_label, **item})
            for index, item in enumerate(event.datalayer_events[:20], 1):
                datalayer_rows.append({
                    "URL": event.url, "Action": event.action_label, "Index": index,
                    "Event": item.get("event") if isinstance(item, dict) else "",
                    "Data": json.dumps(item, default=str),
                })

        skipped_rows: List[Dict[str, Any]] = []
        for si in audit.skipped_interactions:
            extra_str = ""
            if si.interaction_type == "Region_Selector":
                opts = si.extra.get("options", [])
                extra_str = f"Available options ({si.extra.get('available_count', len(opts))}): {', '.join(opts[:15])}"
                if si.extra.get("select_name"):
                    extra_str += f" | Element name/id: {si.extra['select_name']}"
            elif si.interaction_type == "External_Link":
                extra_str = f"External domain: {si.extra.get('external_domain', '')}"
            elif si.interaction_type == "Hidden":
                extra_str = f"Hidden reason: {si.extra.get('hidden_reason', '')}"
            skipped_rows.append({
                "Page URL": si.page_url, "Interaction Type": si.interaction_type,
                "Label": si.label, "Href / Target": si.href, "HTML Tag": si.tag,
                "Reason Skipped": si.reason, "Extra Info": extra_str,
                "Tracked": si.tracked, "Tracked Notes": si.tracked_notes,
            })

        for log in audit.network_logs:
            network_log_rows.append({
                "Timestamp": log.timestamp, "Page URL": log.page_url, "Journey Action": log.action_label,
                "Vendor": log.vendor, "Category": log.category, "Platform": log.platform,
                "Event Name": log.event_name, "Measurement ID": log.measurement_id, "Client ID": log.client_id,
                "Method": log.method, "Response Status": log.response_status, "Request URL": log.request_url,
                "Request POST Data": log.request_post_data[:500] if log.request_post_data else "",
                "Parsed Params (JSON)": json.dumps(log.parsed_params, default=str)[:2000],
            })

        summary = [{
            "Site URL": audit.site_url, "Generated At": audit.generated_at, "Website Type": audit.website_type,
            "Pages Discovered": len(audit.discovered_urls), "Pages Audited": audit.pages_audited,
            "Overall Website Score": audit.overall_score,
            "Critical Issues": sum(1 for page in audit.pages for item in page.issues if item.get("severity") == "CRITICAL"),
            "High Issues": sum(1 for page in audit.pages for item in page.issues if item.get("severity") == "HIGH"),
            "Medium Issues": sum(1 for page in audit.pages for item in page.issues if item.get("severity") == "MEDIUM"),
            "Journey Actions Audited": len(audit.journey_events),
            "GA4 Journey Hits": sum(len(event.ga4_events) for event in audit.journey_events),
            "Adobe Journey Hits": sum(len(event.adobe_events) for event in audit.journey_events),
            "Martech Network Log Entries": len(audit.network_logs),
            "Conversion / Thank-You Events": sum(1 for event in audit.journey_events if is_thank_you_url(event.url)),
            "Skipped Interactions (Total)": len(audit.skipped_interactions),
            "Skipped: Destructive": sum(1 for s in audit.skipped_interactions if s.interaction_type == "Destructive"),
            "Skipped: Auth Gated": sum(1 for s in audit.skipped_interactions if s.interaction_type == "Auth_Gated"),
            "Skipped: Hidden": sum(1 for s in audit.skipped_interactions if s.interaction_type == "Hidden"),
            "Skipped: Region Selectors": sum(1 for s in audit.skipped_interactions if s.interaction_type == "Region_Selector"),
            "Skipped: External Links": sum(1 for s in audit.skipped_interactions if s.interaction_type == "External_Link"),
            "Tracked (Hidden/Auth/External)": sum(1 for s in audit.skipped_interactions if s.tracked),
        }]

        journey_columns = ["URL", "Action Type", "Action Label", "Target", "Success", "GA4 Hit Count", "Adobe Hit Count", "DataLayer Recent Count", "Network Log Count", "Total Network Count", "Notes"]
        ga4_columns = ["URL", "Action", "measurement_id", "event_name", "page_location", "page_title", "client_id", "session_id", "engagement_time_msec", "consent", "raw_url"]
        adobe_columns = ["URL", "Action", "page_name", "channel", "link_name", "link_type", "events", "products", "campaign", "raw_url"]
        datalayer_columns = ["URL", "Action", "Index", "Event", "Data"]
        network_log_columns = ["Timestamp", "Page URL", "Journey Action", "Vendor", "Category", "Platform", "Event Name", "Measurement ID", "Client ID", "Method", "Response Status", "Request URL", "Request POST Data", "Parsed Params (JSON)"]
        skipped_columns = ["Page URL", "Interaction Type", "Label", "Href / Target", "HTML Tag", "Reason Skipped", "Extra Info", "Tracked", "Tracked Notes"]

        with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
            pd.DataFrame(summary).to_excel(writer, sheet_name="Executive_Summary", index=False)
            pd.DataFrame([{"Area": key, "Value": value} for key, value in audit.score_breakdown.items()]).to_excel(writer, sheet_name="Overall_Score_Breakdown", index=False)
            pd.DataFrame(audit.recommendations).to_excel(writer, sheet_name="Recommendations", index=False)
            pd.DataFrame([{"URL": url} for url in audit.discovered_urls]).to_excel(writer, sheet_name="Discovered_URLs", index=False)
            pd.DataFrame(page_rows).to_excel(writer, sheet_name="Page_Audit", index=False)
            pd.DataFrame(platform_rows).to_excel(writer, sheet_name="Detected_Platforms", index=False)
            pd.DataFrame(journey_rows, columns=journey_columns).to_excel(writer, sheet_name="Journey_Audit", index=False)
            pd.DataFrame(ga4_rows, columns=ga4_columns).to_excel(writer, sheet_name="GA4_Events", index=False)
            pd.DataFrame(adobe_rows, columns=adobe_columns).to_excel(writer, sheet_name="Adobe_Events", index=False)
            pd.DataFrame(datalayer_rows, columns=datalayer_columns).to_excel(writer, sheet_name="DataLayer_Events", index=False)
            pd.DataFrame(issue_rows).to_excel(writer, sheet_name="Issues", index=False)
            pd.DataFrame(network_log_rows, columns=network_log_columns).to_excel(writer, sheet_name="Network_Logs", index=False)
            pd.DataFrame(skipped_rows, columns=skipped_columns).to_excel(writer, sheet_name="Skipped_Interactions", index=False)

        format_excel(excel_path)
        return json_path, excel_path


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def ensure_scheme(url: str) -> str:
    url = url.strip()
    return url if url.startswith(("http://", "https://")) else "https://" + url


def normalize_url(url: str, include_query: bool = False) -> str:
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    query = f"?{parsed.query}" if include_query and parsed.query else ""
    return f"{parsed.scheme}://{parsed.netloc}{path}{query}"


def issue(severity: str, category: str, message: str, recommendation: str) -> Dict[str, Any]:
    return {"severity": severity, "category": category, "issue": message, "recommendation": recommendation}


def classify_page_type(url: str, html: str = "") -> str:
    # ThankYou / conversion detection takes priority since it is frequently
    # signalled only via query string (e.g. "?show=thankyou"), which a
    # path-only pattern match would otherwise miss entirely.
    if is_thank_you_url(url):
        return "ThankYou"
    path = urlparse(url).path.lower() or "/"
    full = url.lower()
    for page_type, patterns in PAGE_TYPE_PATTERNS.items():
        if any(re.search(pattern, path, re.I) or re.search(pattern, full, re.I) for pattern in patterns):
            return page_type
    if re.search(r"add to cart|checkout|sku|product", html, re.I):
        return "Product"
    return "General"


def classify_website_type(pages: List[PageAudit]) -> str:
    combined = " ".join(page.url + " " + page.title for page in pages).lower()
    ecommerce_signals = sum(1 for page in pages if page.ecommerce.get("ecommerce_applicable"))
    if ecommerce_signals >= 2:
        return "D2C / Ecommerce" if re.search(r"shop|store|cart|checkout|collection|buy now", combined) else "B2C / Ecommerce"
    b2b = sum(1 for page in pages if page.page_type in ("Contact", "Category", "Content", "Industries") or re.search(r"solutions|services|industries|case-study|whitepaper|demo|contact|enterprise|consulting|resources|insights", page.url + " " + page.title, re.I))
    if b2b >= 2:
        return "B2B / Lead Generation"
    counts = Counter(page.page_type for page in pages)
    if counts.get("Content", 0) >= max(2, len(pages) // 3):
        return "Content / Publisher"
    return "Universal / Corporate"


def is_ga4_request(url: str) -> bool:
    return bool(re.search(r"google-analytics\.com/g/collect|analytics\.google\.com/g/collect", url, re.I))


def parse_ga4_request(url: str) -> Dict[str, Any]:
    query = parse_qs(urlparse(url).query)
    return {
        "measurement_id": first(query.get("tid")) or first(query.get("measurement_id")),
        "event_name": first(query.get("en")) or first(query.get("event")),
        "page_location": first(query.get("dl")),
        "page_title": first(query.get("dt")),
        "client_id": first(query.get("cid")),
        "session_id": first(query.get("sid")),
        "engagement_time_msec": first(query.get("_et")) or first(query.get("engagement_time_msec")),
        "consent": first(query.get("gcs")) or first(query.get("gcd")),
        "raw_url": url[:500],
    }


def is_adobe_request(url: str) -> bool:
    return bool(re.search(r"\.sc\.omtrdc\.net|\.2o7\.net|/b/ss/", url, re.I))


def parse_adobe_request(url: str) -> Dict[str, Any]:
    query = parse_qs(urlparse(url).query)
    data = {
        "page_name": first(query.get("pageName")) or first(query.get("page")),
        "channel": first(query.get("ch")) or first(query.get("channel")),
        "link_name": first(query.get("pev2")) or first(query.get("linkName")),
        "link_type": first(query.get("pe")),
        "events": first(query.get("events")),
        "products": first(query.get("products")),
        "campaign": first(query.get("v0")) or first(query.get("campaign")),
        "raw_url": url[:500],
    }
    for key, values in query.items():
        if re.match(r"^(c|v|prop|eVar)\d+$", key, re.I):
            data[key] = first(values)
    return data


def first(values: Optional[List[str]]) -> str:
    return values[0] if values else ""


def is_risky_click(label: str, href: str) -> bool:
    text = f"{label} {href}".lower()
    blocked = [
        "logout", "log out", "sign out", "delete", "remove", "unsubscribe", "cancel", "clear cart",
        "checkout", "payment", "pay now", "place order", "submit order", "confirm purchase",
        "sign in", "signin", "log in", "login", "register", "create account", "order history",
        "view order", "pay bill", "my account", "reorder",
        "region", "country", "language", "location selector", "region-select",
    ]
    return any(item in text for item in blocked) or href.lower().startswith(("mailto:", "tel:", "javascript:", "#"))


def compute_overall_score(pages: List[PageAudit], journeys: List[JourneyEvent], website_type: str) -> Tuple[int, Dict[str, Any]]:
    total = max(len(pages), 1)
    critical = sum(1 for page in pages for item in page.issues if item.get("severity") == "CRITICAL")
    high = sum(1 for page in pages for item in page.issues if item.get("severity") == "HIGH")
    medium = sum(1 for page in pages for item in page.issues if item.get("severity") == "MEDIUM")
    platform = 100 if any(any(item["platform"] in ("Google Analytics 4", "Adobe Analytics") for item in page.detected_platforms) for page in pages) else 30
    tag_manager = 100 if any(any(item["category"] == "Tag Management" for item in page.detected_platforms) for page in pages) else 40
    consent = int(100 * sum(1 for page in pages if page.consent.get("platform") != "None detected") / total)
    datalayer = int(100 * sum(1 for page in pages if page.datalayer.get("detected")) / total)
    successful = [event for event in journeys if event.success]
    journey_rate = int(100 * sum(1 for event in successful if event.ga4_events or event.adobe_events or event.datalayer_events) / len(successful)) if successful else 50
    penalty = min(45, critical * 12 + high * 6 + medium * 2)
    base = int(platform * 0.20 + tag_manager * 0.15 + consent * 0.20 + datalayer * 0.15 + journey_rate * 0.30)
    score = max(0, min(100, base - penalty))
    return score, {
        "Website Type": website_type, "Platform Coverage": platform, "Tag Manager Coverage": tag_manager,
        "Consent Coverage": consent, "DataLayer Coverage": datalayer, "Journey Hit Rate": journey_rate,
        "Critical Issues": critical, "High Issues": high, "Medium Issues": medium,
        "Issue Penalty": penalty, "Overall Score": score,
    }


def recommendation(priority: str, area: str, finding: str, action: str) -> Dict[str, str]:
    return {"Priority": priority, "Area": area, "Finding": finding, "Recommendation": action}


def generate_overall_recommendations(pages: List[PageAudit], journeys: List[JourneyEvent]) -> List[Dict[str, str]]:
    recommendations = []
    platforms = {item["platform"] for page in pages for item in page.detected_platforms}
    if "Google Analytics 4" not in platforms and "Adobe Analytics" not in platforms:
        recommendations.append(recommendation("Critical", "Analytics Platform", "No primary analytics platform detected consistently", "Implement or validate GA4 or Adobe Analytics across key pages."))
    if not any(item["category"] == "Tag Management" for page in pages for item in page.detected_platforms):
        recommendations.append(recommendation("High", "Tag Management", "No tag management platform detected", "Implement GTM or Adobe Launch for centralised governance."))
    missing_cmp = [page for page in pages if page.consent.get("platform") == "None detected"]
    if missing_cmp:
        recommendations.append(recommendation("Critical", "Consent", f"CMP missing on {len(missing_cmp)} pages", "Implement CMP and consent gating."))
    form_gaps = [page for page in pages if page.forms.get("has_gap")]
    if form_gaps:
        recommendations.append(recommendation("High", "Lead Tracking", f"Form tracking gap found on {len(form_gaps)} pages", "Implement form_start, form_submit and generate_lead."))
    no_hits = [event for event in journeys if event.success and not (event.ga4_events or event.adobe_events or event.datalayer_events)]
    if no_hits:
        recommendations.append(recommendation("High", "Journey Events", f"{len(no_hits)} successful interactions produced no observable analytics hits", "Validate event triggers for clicks, scrolls, forms and downloads."))
    thank_you_events = [event for event in journeys if is_thank_you_url(event.url)]
    thank_you_without_hits = [event for event in thank_you_events if not (event.ga4_events or event.adobe_events)]
    if thank_you_without_hits:
        recommendations.append(recommendation("Critical", "Conversion Tracking", f"{len(thank_you_without_hits)} conversion/thank-you page(s) reached without an observed GA4/Adobe hit", "Validate that conversion pixels fire reliably on all lead-gen thank-you pages."))
    return recommendations or [recommendation("Info", "Overall", "No major gaps detected in the automated audit", "Proceed with manual business-rule and analytics-admin validation.")]


def format_excel(path: str) -> None:
    if load_workbook is None:
        return
    try:
        workbook = load_workbook(path)

        tab_colours: Dict[str, str] = {
            "Network_Logs": "0D4A2E",
            "Skipped_Interactions": "4A235A",
            "GA4_Events": "1A5276",
            "Adobe_Events": "6E2F1A",
            "Issues": "78281F",
        }
        default_fill_hex = "1F4E78"

        fill_cache: Dict[str, Any] = {}
        font_white_bold = Font(color="FFFFFF", bold=True)

        for sheet in workbook.worksheets:
            hex_colour = tab_colours.get(sheet.title, default_fill_hex)
            if hex_colour not in fill_cache:
                fill_cache[hex_colour] = PatternFill(start_color=hex_colour, end_color=hex_colour, fill_type="solid")
            header_fill = fill_cache[hex_colour]

            sheet.freeze_panes = "A2"
            for cell in sheet[1]:
                cell.fill = header_fill
                cell.font = font_white_bold
                cell.alignment = Alignment(horizontal="center", vertical="center")
            for column in sheet.columns:
                width = min(max(max(len(str(cell.value or "")) for cell in column) + 2, 12), 70)
                sheet.column_dimensions[column[0].column_letter].width = width
            for row in sheet.iter_rows():
                for cell in row:
                    cell.alignment = Alignment(vertical="top", wrap_text=True)

        workbook.save(path)
    except Exception as exc:
        print(f"Excel formatting skipped: {exc}")


def select_journey_urls(pages: List[PageAudit], maximum: int) -> List[str]:
    priority = ["Homepage", "Product", "Category", "Industries", "Contact", "Cart", "Checkout", "Content", "Support", "General"]
    selected = []
    for page_type in priority:
        for page in pages:
            if page.page_type == page_type and page.url not in selected:
                selected.append(page.url)
                break
    for page in pages:
        if page.url not in selected:
            selected.append(page.url)
        if len(selected) >= maximum:
            break
    return selected[:maximum]


def sample_value_for_field(name: str, input_type: str) -> str:
    name = (name or "").lower()
    if "email" in name or input_type == "email":
        return "test@example.com"
    if "phone" in name or "mobile" in name or input_type == "tel":
        return "9999999999"
    if "company" in name:
        return "Test Company"
    if "name" in name:
        return "Test User"
    if "message" in name or "comment" in name:
        return "Test message for analytics audit dry run."
    return "Test"


def print_summary(audit: WebsiteAudit) -> None:
    print("\nEnterprise Martech Audit Summary")
    print(f"Site:                        {audit.site_url}")
    print(f"Website Type:                {audit.website_type}")
    print(f"Pages Audited:               {audit.pages_audited}")
    print(f"Overall Score:               {audit.overall_score}/100")
    print(f"Martech Network Log Entries: {len(audit.network_logs)}")
    thank_you_events = [event for event in audit.journey_events if is_thank_you_url(event.url)]
    if thank_you_events:
        print(f"Conversion / Thank-You Pages Reached: {len(thank_you_events)}")
        for event in thank_you_events[:10]:
            hit_summary = f"GA4={len(event.ga4_events)} Adobe={len(event.adobe_events)}"
            print(f"    {event.url}  ({hit_summary})")
    if audit.network_logs:
        vendors = Counter(log.vendor for log in audit.network_logs)
        print("  Vendors observed:")
        for vendor, count in vendors.most_common():
            print(f"    {vendor}: {count} request(s)")
    if audit.skipped_interactions:
        by_type = Counter(s.interaction_type for s in audit.skipped_interactions)
        print(f"\nSkipped Interactions: {len(audit.skipped_interactions)} total")
        labels = {
            "Destructive": "Destructive actions",
            "Auth_Gated": "Auth-gated elements",
            "Hidden": "Hidden elements",
            "Region_Selector": "Region/language selectors",
            "External_Link": "External links",
        }
        for itype, label in labels.items():
            if by_type.get(itype):
                print(f"  {label}: {by_type[itype]}")
        tracked_count = sum(1 for s in audit.skipped_interactions if s.tracked)
        if tracked_count:
            print(f"  -> Tracked (clicked safely): {tracked_count}")
        region_entries = [s for s in audit.skipped_interactions if s.interaction_type == "Region_Selector"]
        if region_entries:
            opts = region_entries[0].extra.get("options", [])
            print("  -> Re-run with --region \"<value>\" to select a locale.")
            if opts:
                print(f"    First selector options: {', '.join(opts[:8])}")
    print("\nTop Recommendations:")
    for item in audit.recommendations[:10]:
        print(f"  [{item['Priority']}] {item['Area']}: {item['Recommendation']}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_platform(args: argparse.Namespace) -> None:
    target = ensure_scheme(args.site or args.url)
    discovered = [target] if args.url else WebsiteCrawler(target, max_pages=args.max_pages, include_query=args.include_query).discover()
    if not discovered:
        print("No pages discovered.")
        return
    print(f"Enterprise Martech Audit Platform: {target}")
    print(f"Pages discovered: {len(discovered)}")
    auditor = PageAuditor()
    pages: List[PageAudit] = []
    if RICH_AVAILABLE:
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(), console=console) as progress:
            task = progress.add_task("Auditing pages...", total=len(discovered))
            for url in discovered:
                pages.append(auditor.audit(url, deep=args.deep))
                progress.advance(task)
    else:
        pages = [auditor.audit(url, deep=args.deep) for url in discovered]
    journeys: List[JourneyEvent] = []
    all_skipped: List[SkippedInteraction] = []
    if args.journey_audit:
        urls = select_journey_urls(pages, args.max_journey_pages)
        journey_auditor = JourneyAuditor(
            submit_forms=args.submit_forms,
            max_clicks_per_page=args.max_clicks_per_page,
            region=args.region,
            track_skipped_interactions=args.track_skipped_interactions,
            max_tracked_per_type=args.max_tracked_per_type,
        )
        for index, url in enumerate(urls, 1):
            print(f"Journey audit {index}/{len(urls)}: {url}")
            page_events, page_skipped = journey_auditor.audit_page(url)
            journeys.extend(page_events)
            all_skipped.extend(page_skipped)
    audit = ReportBuilder().build_website_audit(target, pages, journeys, discovered, skipped_interactions=all_skipped)
    print_summary(audit)
    if args.report:
        json_path, excel_path = ReportBuilder().export(audit, output_dir=args.output_dir)
        print(f"JSON Report Saved:  {json_path}")
        print(f"Excel Report Saved: {excel_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enterprise End-to-End Martech Audit Platform")
    parser.add_argument("--site", help="Full website audit starting from this domain")
    parser.add_argument("--url", help="Single-page audit")
    parser.add_argument("--deep", action="store_true", help="Use Playwright rendering for page audit")
    parser.add_argument("--journey-audit", action="store_true", help="Run the E2E journey audit")
    parser.add_argument("--submit-forms", action="store_true", help="Submit forms. Disabled by default.")
    parser.add_argument("--report", action="store_true", help="Save JSON and Excel reports")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output folder")
    parser.add_argument("--max-pages", type=int, default=0, help="Maximum pages. 0 means all discoverable pages.")
    parser.add_argument("--max-journey-pages", type=int, default=15, help="Maximum pages for journey audit")
    parser.add_argument("--max-clicks-per-page", type=int, default=8, help="Maximum safe clicks per journey page")
    parser.add_argument("--include-query", action="store_true", help="Keep query strings as unique URLs")
    parser.add_argument("--no-playwright", action="store_true", help="Disable Playwright")
    parser.add_argument(
        "--region", default="",
        help=(
            "Region/country/language to select when a locale selector is detected "
            "(e.g. 'United States', 'United Kingdom', 'DE'). "
            "If omitted, selectors are logged but not interacted with."
        ),
    )
    parser.add_argument(
        "--track-skipped-interactions", action="store_true",
        help=(
            "Safely track (rather than only log) Hidden, Auth_Gated and "
            "External_Link items: Hidden elements are revealed via the "
            "mobile menu toggle and clicked; Auth_Gated and External_Link "
            "elements are clicked far enough to capture the analytics beacon "
            "then the auditor returns to the original page WITHOUT entering "
            "credentials, submitting anything, or interacting with the "
            "external site. Destructive items are never tracked."
        ),
    )
    parser.add_argument(
        "--max-tracked-per-type", type=int, default=5,
        help="Maximum number of Hidden/Auth_Gated/External_Link items tracked per page, per category (default 5).",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    global PLAYWRIGHT_AVAILABLE
    if args.no_playwright:
        PLAYWRIGHT_AVAILABLE = False
    if not args.site and not args.url:
        parser.print_help()
        print(
            "\nQuick start:\n"
            "  python martech_tag_auditor.py --site https://example.com --deep --journey-audit --report --max-pages 10\n"
            "\nWith region selection:\n"
            "  python martech_tag_auditor.py --site https://example.com --journey-audit --report --region \"United States\"\n"
            "\nWith tracking of hidden/auth-gated/external links:\n"
            "  python martech_tag_auditor.py --site https://example.com --journey-audit --report --track-skipped-interactions"
        )
        return
    run_platform(args)


if __name__ == "__main__":
    main()
