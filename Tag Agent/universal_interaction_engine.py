#!/usr/bin/env python3
"""Universal website interaction discovery and journey audit engine.

This module is website-agnostic. It discovers menus, mega menus, cards,
tabs, accordions, carousels, forms, downloads, media, search, filters and
commerce controls from live DOM behaviour rather than website-specific labels.

Integration:
    from universal_interaction_engine import UniversalInteractionEngine

    engine = UniversalInteractionEngine(
        max_candidates=120,
        max_actions=30,
        allow_add_to_cart=False,
        submit_forms=False,
    )
    candidates = engine.discover(page)
    events = engine.execute(page, candidates, raw_network)

The caller should attach Playwright request/response listeners to raw_network.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urldefrag, urljoin, urlparse


@dataclass
class InteractionCandidate:
    candidate_id: str
    source_url: str
    interaction_type: str
    label: str
    description: str = ""
    href: str = ""
    selector: str = ""
    parent_label: str = ""
    section_label: str = ""
    trigger_method: str = "click"
    trigger_selector: str = ""
    destination_domain: str = ""
    is_visible: bool = False
    is_enabled: bool = False
    is_internal: bool = True
    is_destructive: bool = False
    is_auth_gated: bool = False
    priority: int = 0
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class InteractionResult:
    candidate_id: str
    source_url: str
    destination_url: str
    destination_page_type: str
    interaction_type: str
    parent_label: str
    section_label: str
    action_label: str
    description: str
    target: str
    success: bool
    trigger_method: str
    network_requests: List[Dict[str, Any]] = field(default_factory=list)
    datalayer_events: List[Any] = field(default_factory=list)
    conversion_detected: bool = False
    notes: str = ""


class UniversalInteractionEngine:
    """Behaviour-driven, website-agnostic interaction engine."""

    NAV_TRIGGER_SELECTORS = [
        "nav button",
        "nav a",
        "header button",
        "header a",
        "[aria-haspopup]",
        "[aria-expanded]",
        "[aria-controls]",
        "[role='menuitem']",
        "[role='tab']",
        "button[class*='menu' i]",
        "button[class*='nav' i]",
        "button[class*='dropdown' i]",
        "a[class*='menu' i]",
        "a[class*='dropdown' i]",
    ]

    CARD_SELECTORS = [
        "article",
        "[class*='card' i]",
        "[class*='tile' i]",
        "[class*='teaser' i]",
        "[class*='product' i]",
        "[class*='category' i]",
        "[class*='result' i]",
        "li",
    ]

    COMMERCE_SELECTORS = [
        "button:has-text('Add to Cart')",
        "button:has-text('Add to Bag')",
        "button:has-text('Buy Now')",
        "[data-action*='cart' i]",
        "[class*='add-to-cart' i]",
        "[aria-label*='cart' i]",
        "select[name*='quantity' i]",
        "select[name*='variant' i]",
        "[class*='variant' i] button",
    ]

    TAB_SELECTORS = [
        "[role='tab']",
        "[class*='tabs' i] button",
        "[class*='tab-list' i] button",
    ]

    ACCORDION_SELECTORS = [
        "button[aria-expanded]",
        "summary",
        "[class*='accordion' i] button",
        "[class*='collapse' i] button",
    ]

    CAROUSEL_SELECTORS = [
        "[aria-label*='next' i]",
        "[aria-label*='previous' i]",
        "[aria-label*='prev' i]",
        "[class*='carousel' i] button",
        "[class*='slider' i] button",
        "[class*='swiper' i] button",
    ]

    SEARCH_FILTER_SELECTORS = [
        "input[type='search']",
        "[role='search'] input",
        "button[aria-label*='search' i]",
        "select[name*='filter' i]",
        "[class*='filter' i] button",
        "[aria-label*='filter' i]",
    ]

    PERMANENT_BLOCK_PATTERN = re.compile(
        r"log.?out|sign.?out|delete|remove.?account|unsubscribe|clear.?cart|"
        r"place.?order|submit.?order|confirm.?purchase|complete.?purchase|"
        r"pay.?now|submit.?payment|cancel.?subscription|close.?account",
        re.I,
    )

    AUTH_PATTERN = re.compile(
        r"sign.?in|log.?in|register|create.?account|order.?history|"
        r"view.?order|pay.?bill|my.?account|account.?dashboard|reorder",
        re.I,
    )

    CONVERSION_PATTERN = re.compile(
        r"thank.?you|confirmation|submitted|success|complete|"
        r"show=thankyou|status=success|generate_lead|form.?success|purchase",
        re.I,
    )

    DOWNLOAD_PATTERN = re.compile(
        r"\.(pdf|doc|docx|xls|xlsx|ppt|pptx|zip|csv)(\?|#|$)", re.I
    )

    MEDIA_PATTERN = re.compile(
        r"video|audio|podcast|webinar|play|pause|listen|watch", re.I
    )

    def __init__(
        self,
        max_candidates: int = 120,
        max_actions: int = 30,
        menu_wait_ms: int = 850,
        action_wait_ms: int = 2500,
        allow_add_to_cart: bool = False,
        submit_forms: bool = False,
        allow_external_navigation: bool = False,
    ) -> None:
        self.max_candidates = max_candidates
        self.max_actions = max_actions
        self.menu_wait_ms = menu_wait_ms
        self.action_wait_ms = action_wait_ms
        self.allow_add_to_cart = allow_add_to_cart
        self.submit_forms = submit_forms
        self.allow_external_navigation = allow_external_navigation
        self.tested_keys: Set[Tuple[str, str, str, str]] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discover(self, page: Any) -> List[InteractionCandidate]:
        """Discover, classify, deduplicate and prioritise interactions."""
        candidates: List[InteractionCandidate] = []
        candidates.extend(self._discover_expandable_navigation(page))
        candidates.extend(self._discover_cards(page))
        candidates.extend(self._discover_commerce_controls(page))
        candidates.extend(self._discover_tabs(page))
        candidates.extend(self._discover_accordions(page))
        candidates.extend(self._discover_carousels(page))
        candidates.extend(self._discover_forms(page))
        candidates.extend(self._discover_downloads_and_media(page))
        candidates.extend(self._discover_search_and_filters(page))
        candidates.extend(self._discover_visible_links_and_ctas(page))

        classified = [self._classify_candidate(item) for item in candidates]
        deduped = self._deduplicate(classified)
        deduped.sort(key=lambda item: (item.priority, item.confidence), reverse=True)
        return deduped[: self.max_candidates]

    def execute(
        self,
        page: Any,
        candidates: List[InteractionCandidate],
        raw_network: Optional[List[Dict[str, Any]]] = None,
    ) -> List[InteractionResult]:
        """Execute prioritised interactions safely and capture outcomes."""
        raw_network = raw_network if raw_network is not None else []
        results: List[InteractionResult] = []

        for candidate in candidates:
            if len(results) >= self.max_actions:
                break
            key = self._candidate_key(candidate)
            if key in self.tested_keys:
                continue
            self.tested_keys.add(key)

            if candidate.is_destructive:
                results.append(self._blocked_result(candidate, "Permanent safety block"))
                continue
            if candidate.interaction_type == "Add_To_Cart" and not self.allow_add_to_cart:
                results.append(self._blocked_result(candidate, "Add to cart disabled"))
                continue
            if candidate.interaction_type == "Form_Submit" and not self.submit_forms:
                results.append(self._blocked_result(candidate, "Form submission disabled; dry-run only"))
                continue
            if not candidate.is_internal and not self.allow_external_navigation:
                results.append(self._capture_external_click(page, candidate, raw_network))
                continue

            results.append(self._execute_candidate(page, candidate, raw_network))

        return results

    def export_discovery_json(
        self,
        path: str,
        candidates: List[InteractionCandidate],
        results: Optional[List[InteractionResult]] = None,
    ) -> None:
        payload = {
            "candidates": [asdict(item) for item in candidates],
            "results": [asdict(item) for item in (results or [])],
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, default=str)

    # ------------------------------------------------------------------
    # DOM state and generic element helpers
    # ------------------------------------------------------------------

    def _visible_interaction_snapshot(self, page: Any) -> Dict[str, Dict[str, Any]]:
        script = r"""
        () => {
          const nodes = Array.from(document.querySelectorAll(
            "a[href], button, input[type='button'], input[type='submit'], " +
            "[role='menuitem'], [role='option'], [role='tab'], summary"
          ));
          const result = {};
          for (const element of nodes) {
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            const visible = rect.width > 5 && rect.height > 5 &&
              style.display !== 'none' && style.visibility !== 'hidden' &&
              Number(style.opacity || 1) > 0 && element.getAttribute('aria-hidden') !== 'true';
            if (!visible) continue;
            const label = (
              element.getAttribute('aria-label') ||
              element.getAttribute('title') ||
              element.getAttribute('value') ||
              element.textContent || ''
            ).replace(/\s+/g, ' ').trim().slice(0, 180);
            const href = element.getAttribute('href') || '';
            if (!label) continue;
            const key = `${label.toLowerCase()}|${href}|${element.tagName}`;
            result[key] = {
              label, href,
              tag: element.tagName.toLowerCase(),
              role: element.getAttribute('role') || '',
              ariaExpanded: element.getAttribute('aria-expanded') || '',
              ariaControls: element.getAttribute('aria-controls') || ''
            };
          }
          return result;
        }
        """
        try:
            return page.evaluate(script) or {}
        except Exception:
            return {}

    @staticmethod
    def _stable_id(*parts: str) -> str:
        raw = "|".join(value or "" for value in parts)
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]

    @staticmethod
    def _normalise_label(value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    @staticmethod
    def _normalise_url(value: str) -> str:
        if not value:
            return ""
        clean, _ = urldefrag(value)
        parsed = urlparse(clean)
        path = parsed.path.rstrip("/") or "/"
        return f"{parsed.scheme}://{parsed.netloc}{path}" if parsed.scheme else path

    @staticmethod
    def _is_internal(source_url: str, href: str) -> bool:
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            return True
        absolute = urljoin(source_url, href)
        source_host = urlparse(source_url).netloc.lower().replace("www.", "")
        target_host = urlparse(absolute).netloc.lower().replace("www.", "")
        return source_host == target_host

    def _candidate_key(self, item: InteractionCandidate) -> Tuple[str, str, str, str]:
        return (
            item.interaction_type.lower(),
            self._normalise_label(item.label).lower(),
            self._normalise_url(item.href).lower(),
            self._normalise_label(item.parent_label).lower(),
        )

    def _deduplicate(self, candidates: List[InteractionCandidate]) -> List[InteractionCandidate]:
        best: Dict[Tuple[str, str, str, str], InteractionCandidate] = {}
        for candidate in candidates:
            key = self._candidate_key(candidate)
            existing = best.get(key)
            if existing is None or (candidate.priority, candidate.confidence) > (existing.priority, existing.confidence):
                best[key] = candidate
        return list(best.values())

    # ------------------------------------------------------------------
    # Behaviour-driven navigation discovery
    # ------------------------------------------------------------------

    def _discover_navigation_triggers(self, page: Any) -> List[Dict[str, Any]]:
        selectors_json = json.dumps(self.NAV_TRIGGER_SELECTORS)
        script = rf"""
        () => {{
          const selectors = {selectors_json};
          const nodes = Array.from(document.querySelectorAll(selectors.join(',')));
          const results = [];
          const seen = new Set();
          for (const element of nodes) {{
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            const visible = rect.width > 5 && rect.height > 5 &&
              style.display !== 'none' && style.visibility !== 'hidden' &&
              Number(style.opacity || 1) > 0;
            if (!visible) continue;
            const label = (
              element.getAttribute('aria-label') ||
              element.getAttribute('title') ||
              element.textContent || ''
            ).replace(/\s+/g, ' ').trim().slice(0, 150);
            if (!label) continue;
            const href = element.getAttribute('href') || '';
            const key = [element.tagName, label, href,
              element.getAttribute('aria-controls') || ''].join('|');
            if (seen.has(key)) continue;
            seen.add(key);
            const id = `universal-nav-${{results.length}}`;
            element.setAttribute('data-universal-nav-id', id);
            results.push({{
              selector: `[data-universal-nav-id="${{id}}"]`,
              label,
              tag: element.tagName.toLowerCase(),
              href,
              ariaExpanded: element.getAttribute('aria-expanded') || '',
              ariaHaspopup: element.getAttribute('aria-haspopup') || '',
              ariaControls: element.getAttribute('aria-controls') || '',
              role: element.getAttribute('role') || ''
            }});
          }}
          return results;
        }}
        """
        try:
            return page.evaluate(script) or []
        except Exception:
            return []

    def _discover_expandable_navigation(self, page: Any) -> List[InteractionCandidate]:
        candidates: List[InteractionCandidate] = []
        triggers = self._discover_navigation_triggers(page)

        for trigger in triggers:
            selector = trigger.get("selector", "")
            if not selector:
                continue
            locator = page.locator(selector).first
            before = self._visible_interaction_snapshot(page)
            method = ""
            after = before

            try:
                locator.hover(timeout=2000)
                page.wait_for_timeout(self.menu_wait_ms)
                after = self._visible_interaction_snapshot(page)
                if set(after) - set(before):
                    method = "hover"
            except Exception:
                pass

            if not method:
                try:
                    locator.click(timeout=2000)
                    page.wait_for_timeout(self.menu_wait_ms)
                    after = self._visible_interaction_snapshot(page)
                    if set(after) - set(before):
                        method = "click"
                except Exception:
                    pass

            if not method:
                self._close_open_navigation(page)
                continue

            for key in set(after) - set(before):
                child = after[key]
                label = self._normalise_label(child.get("label", ""))
                href = child.get("href", "")
                if not label:
                    continue
                section = self._infer_section_for_visible_link(page, label, href)
                absolute_href = urljoin(page.url, href) if href else ""
                candidates.append(
                    InteractionCandidate(
                        candidate_id=self._stable_id(page.url, trigger.get("label", ""), label, absolute_href),
                        source_url=page.url,
                        interaction_type="Expandable_Navigation_Link",
                        label=label,
                        description="",
                        href=absolute_href,
                        selector="",
                        parent_label=trigger.get("label", ""),
                        section_label=section,
                        trigger_method=method,
                        trigger_selector=selector,
                        destination_domain=urlparse(absolute_href).netloc,
                        is_visible=True,
                        is_enabled=True,
                        is_internal=self._is_internal(page.url, absolute_href),
                        priority=70,
                        confidence=0.95,
                        metadata={"snapshot_key": key, "child_tag": child.get("tag", "")},
                    )
                )

            self._close_open_navigation(page)

        return candidates

    def _infer_section_for_visible_link(self, page: Any, label: str, href: str) -> str:
        payload = {"label": label, "href": href}
        script = r"""
        payload => {
          const links = Array.from(document.querySelectorAll('a[href], button, [role="menuitem"]'));
          const target = links.find(el => {
            const text = (el.textContent || el.getAttribute('aria-label') || '')
              .replace(/\s+/g, ' ').trim();
            const href = el.getAttribute('href') || '';
            return text === payload.label && (!payload.href || href === payload.href || el.href === payload.href);
          });
          if (!target) return '';
          let current = target.parentElement;
          for (let depth = 0; depth < 7 && current; depth++) {
            const headings = Array.from(current.querySelectorAll(':scope > h1, :scope > h2, :scope > h3, :scope > h4, :scope > h5, :scope > h6, :scope > [role="heading"], :scope > legend'));
            if (headings.length) {
              return (headings[0].textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120);
            }
            current = current.parentElement;
          }
          return '';
        }
        """
        try:
            return page.evaluate(script, payload) or ""
        except Exception:
            return ""

    @staticmethod
    def _close_open_navigation(page: Any) -> None:
        try:
            page.locator("body").hover(position={"x": 5, "y": 500}, timeout=1200)
            page.wait_for_timeout(350)
        except Exception:
            pass
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(250)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Universal component discovery
    # ------------------------------------------------------------------

    def _discover_cards(self, page: Any) -> List[InteractionCandidate]:
        selectors_json = json.dumps(self.CARD_SELECTORS)
        script = rf"""
        () => {{
          const containers = Array.from(document.querySelectorAll({selectors_json}.join(',')));
          const cards = [];
          const seen = new Set();
          for (const container of containers) {{
            const link = container.querySelector('a[href]');
            if (!link) continue;
            const rect = container.getBoundingClientRect();
            const style = window.getComputedStyle(container);
            if (rect.width < 80 || rect.height < 45 || style.display === 'none' || style.visibility === 'hidden') continue;
            const heading = container.querySelector('h1,h2,h3,h4,h5,h6,[role="heading"]');
            const label = (
              heading?.textContent || link.getAttribute('aria-label') ||
              link.getAttribute('title') || link.textContent || ''
            ).replace(/\s+/g, ' ').trim().slice(0, 150);
            if (!label) continue;
            const descriptionElement = container.querySelector('p,[class*="description" i],[class*="subtitle" i],[class*="copy" i]');
            const description = (descriptionElement?.textContent || '')
              .replace(/\s+/g, ' ').trim().slice(0, 300);
            const href = link.getAttribute('href') || '';
            const key = `${{label.toLowerCase()}}|${{href}}`;
            if (seen.has(key)) continue;
            seen.add(key);
            const id = `universal-card-${{cards.length}}`;
            link.setAttribute('data-universal-card-id', id);
            cards.push({{
              selector: `[data-universal-card-id="${{id}}"]`,
              label, description, href,
              hasImage: Boolean(container.querySelector('img,picture,svg')),
              className: String(container.className || ''),
              itemType: container.getAttribute('itemtype') || ''
            }});
          }}
          return cards;
        }}
        """
        try:
            rows = page.evaluate(script) or []
        except Exception:
            rows = []

        return [
            InteractionCandidate(
                candidate_id=self._stable_id(page.url, "card", row.get("label", ""), row.get("href", "")),
                source_url=page.url,
                interaction_type="Content_Card",
                label=row.get("label", ""),
                description=row.get("description", ""),
                href=urljoin(page.url, row.get("href", "")),
                selector=row.get("selector", ""),
                destination_domain=urlparse(urljoin(page.url, row.get("href", ""))).netloc,
                is_visible=True,
                is_enabled=True,
                is_internal=self._is_internal(page.url, row.get("href", "")),
                priority=65,
                confidence=0.80,
                metadata={
                    "has_image": row.get("hasImage", False),
                    "class_name": row.get("className", ""),
                    "item_type": row.get("itemType", ""),
                },
            )
            for row in rows
        ]

    def _discover_commerce_controls(self, page: Any) -> List[InteractionCandidate]:
        return self._discover_by_selectors(page, self.COMMERCE_SELECTORS, "Commerce_Control", 90, 0.92)

    def _discover_tabs(self, page: Any) -> List[InteractionCandidate]:
        return self._discover_by_selectors(page, self.TAB_SELECTORS, "Tab_Control", 55, 0.90)

    def _discover_accordions(self, page: Any) -> List[InteractionCandidate]:
        return self._discover_by_selectors(page, self.ACCORDION_SELECTORS, "Accordion_Control", 50, 0.88)

    def _discover_carousels(self, page: Any) -> List[InteractionCandidate]:
        return self._discover_by_selectors(page, self.CAROUSEL_SELECTORS, "Carousel_Control", 45, 0.85)

    def _discover_search_and_filters(self, page: Any) -> List[InteractionCandidate]:
        return self._discover_by_selectors(page, self.SEARCH_FILTER_SELECTORS, "Search_Filter_Control", 60, 0.85)

    def _discover_by_selectors(
        self,
        page: Any,
        selectors: List[str],
        interaction_type: str,
        priority: int,
        confidence: float,
    ) -> List[InteractionCandidate]:
        results: List[InteractionCandidate] = []
        seen: Set[Tuple[str, str]] = set()
        for selector in selectors:
            try:
                locators = page.locator(selector)
                count = min(locators.count(), 40)
            except Exception:
                continue
            for index in range(count):
                locator = locators.nth(index)
                try:
                    if not locator.is_visible(timeout=300):
                        continue
                    label = self._normalise_label(
                        locator.get_attribute("aria-label")
                        or locator.get_attribute("title")
                        or locator.get_attribute("value")
                        or locator.inner_text(timeout=500)
                        or ""
                    )[:150]
                    href = locator.get_attribute("href") or ""
                    if not label:
                        label = interaction_type
                    key = (label.lower(), href)
                    if key in seen:
                        continue
                    seen.add(key)
                    marker = self._stable_id(page.url, interaction_type, label, href)
                    locator.evaluate("(el, id) => el.setAttribute('data-universal-control-id', id)", marker)
                    target_selector = f'[data-universal-control-id="{marker}"]'
                    results.append(
                        InteractionCandidate(
                            candidate_id=marker,
                            source_url=page.url,
                            interaction_type=interaction_type,
                            label=label,
                            href=urljoin(page.url, href) if href else "",
                            selector=target_selector,
                            destination_domain=urlparse(urljoin(page.url, href)).netloc if href else "",
                            is_visible=True,
                            is_enabled=locator.is_enabled(timeout=300),
                            is_internal=self._is_internal(page.url, href),
                            priority=priority,
                            confidence=confidence,
                            metadata={"source_selector": selector},
                        )
                    )
                except Exception:
                    continue
        return results

    def _discover_forms(self, page: Any) -> List[InteractionCandidate]:
        results: List[InteractionCandidate] = []
        try:
            forms = page.locator("form")
            count = min(forms.count(), 20)
        except Exception:
            return results

        for index in range(count):
            form = forms.nth(index)
            try:
                if not form.is_visible(timeout=300):
                    continue
                label = self._normalise_label(
                    form.get_attribute("aria-label")
                    or form.get_attribute("name")
                    or form.get_attribute("id")
                    or f"Form {index + 1}"
                )
                marker = self._stable_id(page.url, "form", label, str(index))
                form.evaluate("(el, id) => el.setAttribute('data-universal-form-id', id)", marker)
                submit = form.locator("button[type='submit'],input[type='submit']").first
                submit_label = "Submit"
                if submit.count():
                    submit_label = self._normalise_label(
                        submit.get_attribute("aria-label")
                        or submit.get_attribute("value")
                        or submit.inner_text(timeout=300)
                        or "Submit"
                    )
                results.append(
                    InteractionCandidate(
                        candidate_id=marker,
                        source_url=page.url,
                        interaction_type="Form",
                        label=label,
                        description=submit_label,
                        selector=f'[data-universal-form-id="{marker}"]',
                        is_visible=True,
                        is_enabled=True,
                        is_internal=True,
                        priority=85,
                        confidence=0.92,
                        metadata={"form_index": index, "submit_label": submit_label},
                    )
                )
            except Exception:
                continue
        return results

    def _discover_downloads_and_media(self, page: Any) -> List[InteractionCandidate]:
        results: List[InteractionCandidate] = []
        try:
            links = page.locator("a[href]")
            count = min(links.count(), 500)
        except Exception:
            return results
        for index in range(count):
            locator = links.nth(index)
            try:
                href = locator.get_attribute("href") or ""
                label = self._normalise_label(
                    locator.get_attribute("aria-label")
                    or locator.get_attribute("title")
                    or locator.inner_text(timeout=250)
                    or ""
                )[:150]
                absolute = urljoin(page.url, href)
                interaction_type = ""
                priority = 0
                if self.DOWNLOAD_PATTERN.search(absolute):
                    interaction_type, priority = "Download_Link", 75
                elif self.MEDIA_PATTERN.search(f"{label} {absolute}"):
                    interaction_type, priority = "Media_Link", 55
                if not interaction_type:
                    continue
                marker = self._stable_id(page.url, interaction_type, label, absolute)
                locator.evaluate("(el, id) => el.setAttribute('data-universal-media-id', id)", marker)
                results.append(
                    InteractionCandidate(
                        candidate_id=marker,
                        source_url=page.url,
                        interaction_type=interaction_type,
                        label=label or interaction_type,
                        href=absolute,
                        selector=f'[data-universal-media-id="{marker}"]',
                        destination_domain=urlparse(absolute).netloc,
                        is_visible=locator.is_visible(timeout=250),
                        is_enabled=True,
                        is_internal=self._is_internal(page.url, absolute),
                        priority=priority,
                        confidence=0.9,
                    )
                )
            except Exception:
                continue
        return results

    def _discover_visible_links_and_ctas(self, page: Any) -> List[InteractionCandidate]:
        results: List[InteractionCandidate] = []
        try:
            locators = page.locator("a[href]:visible,button:visible,input[type='button']:visible")
            count = min(locators.count(), 500)
        except Exception:
            return results
        for index in range(count):
            locator = locators.nth(index)
            try:
                label = self._normalise_label(
                    locator.get_attribute("aria-label")
                    or locator.get_attribute("title")
                    or locator.get_attribute("value")
                    or locator.inner_text(timeout=250)
                    or ""
                )[:150]
                href = locator.get_attribute("href") or ""
                if not label:
                    continue
                marker = self._stable_id(page.url, "visible", label, href)
                locator.evaluate("(el, id) => el.setAttribute('data-universal-visible-id', id)", marker)
                results.append(
                    InteractionCandidate(
                        candidate_id=marker,
                        source_url=page.url,
                        interaction_type="Visible_Link_or_CTA",
                        label=label,
                        href=urljoin(page.url, href) if href else "",
                        selector=f'[data-universal-visible-id="{marker}"]',
                        destination_domain=urlparse(urljoin(page.url, href)).netloc if href else "",
                        is_visible=True,
                        is_enabled=locator.is_enabled(timeout=250),
                        is_internal=self._is_internal(page.url, href),
                        priority=35,
                        confidence=0.70,
                    )
                )
            except Exception:
                continue
        return results

    # ------------------------------------------------------------------
    # Classification and prioritisation
    # ------------------------------------------------------------------

    def _classify_candidate(self, candidate: InteractionCandidate) -> InteractionCandidate:
        text = " ".join(
            [
                candidate.label,
                candidate.description,
                candidate.href,
                candidate.parent_label,
                candidate.section_label,
                str(candidate.metadata.get("class_name", "")),
                str(candidate.metadata.get("item_type", "")),
            ]
        ).lower()

        candidate.is_destructive = bool(self.PERMANENT_BLOCK_PATTERN.search(text))
        candidate.is_auth_gated = bool(self.AUTH_PATTERN.search(text))

        if candidate.is_destructive:
            candidate.interaction_type = "Destructive"
            candidate.priority = 0
            candidate.confidence = 1.0
            return candidate

        if re.search(r"add.?to.?(cart|bag)|buy.?now", text, re.I):
            candidate.interaction_type = "Add_To_Cart"
            candidate.priority = 95
            candidate.confidence = max(candidate.confidence, 0.97)
        elif re.search(r"request|quote|demo|contact.?sales|talk.?to.?sales|book.?demo", text, re.I):
            candidate.interaction_type = "Lead_Generation_CTA"
            candidate.priority = 95
            candidate.confidence = max(candidate.confidence, 0.93)
        elif self.CONVERSION_PATTERN.search(text):
            candidate.interaction_type = "Conversion"
            candidate.priority = 100
            candidate.confidence = max(candidate.confidence, 0.95)
        elif re.search(r"/product|/products|/p/|sku|model.?number|itemtype.*product", text, re.I):
            candidate.interaction_type = "Product_Card"
            candidate.priority = 85
            candidate.confidence = max(candidate.confidence, 0.85)
        elif re.search(r"/industr|government|healthcare|education|retail|finance|legal|pharmacy", text, re.I):
            candidate.interaction_type = "Industry_or_Segment_Card"
            candidate.priority = 75
            candidate.confidence = max(candidate.confidence, 0.82)
        elif re.search(r"case.?study|blog|article|webinar|podcast|resource|whitepaper|ebook", text, re.I):
            candidate.interaction_type = "Resource_Card"
            candidate.priority = 65
            candidate.confidence = max(candidate.confidence, 0.82)
        elif self.DOWNLOAD_PATTERN.search(text):
            candidate.interaction_type = "Download_Link"
            candidate.priority = 75
            candidate.confidence = max(candidate.confidence, 0.92)
        elif candidate.is_auth_gated:
            candidate.interaction_type = "Auth_Gated"
            candidate.priority = 30
            candidate.confidence = max(candidate.confidence, 0.88)
        elif candidate.interaction_type == "Expandable_Navigation_Link":
            candidate.priority = 70
        elif candidate.interaction_type == "Content_Card":
            candidate.priority = 65

        if re.search(r"footer|privacy|terms|accessibility|sitemap|copyright", text, re.I):
            candidate.priority = min(candidate.priority, 15)
        return candidate

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _execute_candidate(
        self,
        page: Any,
        candidate: InteractionCandidate,
        raw_network: List[Dict[str, Any]],
    ) -> InteractionResult:
        source_url = page.url
        before_network = len(raw_network)
        before_snapshot = self._visible_interaction_snapshot(page)

        try:
            if candidate.interaction_type == "Form":
                return self._execute_form(page, candidate, raw_network, before_network)

            if candidate.trigger_selector:
                trigger = page.locator(candidate.trigger_selector).first
                if trigger.count() == 0:
                    raise RuntimeError("Navigation trigger no longer exists")
                if candidate.trigger_method == "hover":
                    trigger.hover(timeout=2500)
                else:
                    trigger.click(timeout=2500)
                page.wait_for_timeout(self.menu_wait_ms)

            locator = self._resolve_candidate_locator(page, candidate)
            if locator.count() == 0:
                raise RuntimeError("Candidate element not found")
            if not locator.is_visible(timeout=1200):
                raise RuntimeError("Candidate element is not visible")
            if not locator.is_enabled(timeout=1200):
                raise RuntimeError("Candidate element is disabled")

            locator.scroll_into_view_if_needed(timeout=2000)
            page.wait_for_timeout(200)
            locator.click(timeout=5000)
            page.wait_for_timeout(self.action_wait_ms)

            destination_url = page.url
            after_snapshot = self._visible_interaction_snapshot(page)
            datalayer = self._get_datalayer(page)
            conversion = bool(
                self.CONVERSION_PATTERN.search(destination_url)
                or self._datalayer_has_conversion(datalayer)
            )
            result = InteractionResult(
                candidate_id=candidate.candidate_id,
                source_url=source_url,
                destination_url=destination_url,
                destination_page_type=self._classify_page_type(destination_url, page),
                interaction_type=candidate.interaction_type,
                parent_label=candidate.parent_label,
                section_label=candidate.section_label,
                action_label=candidate.label,
                description=candidate.description,
                target=candidate.href or candidate.selector,
                success=True,
                trigger_method=candidate.trigger_method,
                network_requests=raw_network[before_network:],
                datalayer_events=datalayer,
                conversion_detected=conversion,
                notes=f"Visible interactions before={len(before_snapshot)}, after={len(after_snapshot)}",
            )
            return result
        except Exception as exc:
            return InteractionResult(
                candidate_id=candidate.candidate_id,
                source_url=source_url,
                destination_url=page.url if not page.is_closed() else "",
                destination_page_type="",
                interaction_type=candidate.interaction_type,
                parent_label=candidate.parent_label,
                section_label=candidate.section_label,
                action_label=candidate.label,
                description=candidate.description,
                target=candidate.href or candidate.selector,
                success=False,
                trigger_method=candidate.trigger_method,
                network_requests=raw_network[before_network:],
                notes=str(exc)[:500],
            )
        finally:
            if not page.is_closed() and page.url != source_url:
                self._navigate_back(page, source_url)
            self._close_open_navigation(page)

    def _resolve_candidate_locator(self, page: Any, candidate: InteractionCandidate) -> Any:
        if candidate.selector:
            locator = page.locator(candidate.selector).first
            if locator.count() > 0:
                return locator

        if candidate.href:
            relative = urlparse(candidate.href)
            href_variants = [candidate.href]
            if relative.path:
                local = relative.path
                if relative.query:
                    local += f"?{relative.query}"
                if relative.fragment:
                    local += f"#{relative.fragment}"
                href_variants.append(local)
            for href in href_variants:
                try:
                    escaped = href.replace("\\", "\\\\").replace('"', '\\"')
                    locator = page.locator(f'a[href="{escaped}"]').first
                    if locator.count() > 0:
                        return locator
                except Exception:
                    continue

        try:
            return page.get_by_text(candidate.label, exact=True).first
        except Exception:
            return page.locator("a[href='__universal_no_match__']")

    def _capture_external_click(
        self,
        page: Any,
        candidate: InteractionCandidate,
        raw_network: List[Dict[str, Any]],
    ) -> InteractionResult:
        source_url = page.url
        before = len(raw_network)
        try:
            locator = self._resolve_candidate_locator(page, candidate)
            if locator.count() == 0 or not locator.is_visible(timeout=1000):
                raise RuntimeError("External link not visible")

            # Prevent navigation only; allow the site's click handlers and
            # analytics listeners to execute.
            page.evaluate(
                """
                () => {
                  window.__universalPreventNavigation = event => {
                    const anchor = event.target.closest('a[href]');
                    if (anchor) event.preventDefault();
                  };
                  document.addEventListener('click', window.__universalPreventNavigation, true);
                }
                """
            )
            locator.click(timeout=4000)
            page.wait_for_timeout(1500)
            page.evaluate(
                """
                () => {
                  if (window.__universalPreventNavigation) {
                    document.removeEventListener('click', window.__universalPreventNavigation, true);
                    delete window.__universalPreventNavigation;
                  }
                }
                """
            )
            return InteractionResult(
                candidate_id=candidate.candidate_id,
                source_url=source_url,
                destination_url=candidate.href,
                destination_page_type="External",
                interaction_type="External_Link",
                parent_label=candidate.parent_label,
                section_label=candidate.section_label,
                action_label=candidate.label,
                description=candidate.description,
                target=candidate.href,
                success=True,
                trigger_method=candidate.trigger_method,
                network_requests=raw_network[before:],
                datalayer_events=self._get_datalayer(page),
                notes="Click handlers executed; external navigation prevented.",
            )
        except Exception as exc:
            return InteractionResult(
                candidate_id=candidate.candidate_id,
                source_url=source_url,
                destination_url=candidate.href,
                destination_page_type="External",
                interaction_type="External_Link",
                parent_label=candidate.parent_label,
                section_label=candidate.section_label,
                action_label=candidate.label,
                description=candidate.description,
                target=candidate.href,
                success=False,
                trigger_method=candidate.trigger_method,
                network_requests=raw_network[before:],
                notes=str(exc)[:500],
            )

    def _execute_form(
        self,
        page: Any,
        candidate: InteractionCandidate,
        raw_network: List[Dict[str, Any]],
        before_network: int,
    ) -> InteractionResult:
        form = page.locator(candidate.selector).first
        source_url = page.url
        if form.count() == 0:
            raise RuntimeError("Form not found")

        fields = form.locator("input:visible,textarea:visible,select:visible")
        for index in range(min(fields.count(), 20)):
            field = fields.nth(index)
            try:
                tag = field.evaluate("el => el.tagName.toLowerCase()")
                input_type = (field.get_attribute("type") or "text").lower()
                name = field.get_attribute("name") or field.get_attribute("id") or ""
                if input_type in {"hidden", "submit", "button", "file", "checkbox", "radio"}:
                    continue
                if tag == "select":
                    continue
                field.fill(self._sample_value(name, input_type), timeout=1200)
            except Exception:
                continue

        submitted = False
        if self.submit_forms:
            submit = form.locator("button[type='submit']:visible,input[type='submit']:visible").first
            if submit.count() and submit.is_enabled(timeout=500):
                submit.click(timeout=4000)
                page.wait_for_timeout(self.action_wait_ms)
                submitted = True

        datalayer = self._get_datalayer(page)
        conversion = bool(
            self.CONVERSION_PATTERN.search(page.url)
            or self._datalayer_has_conversion(datalayer)
        )
        return InteractionResult(
            candidate_id=candidate.candidate_id,
            source_url=source_url,
            destination_url=page.url,
            destination_page_type=self._classify_page_type(page.url, page),
            interaction_type="Form_Submit" if submitted else "Form_Dry_Run",
            parent_label=candidate.parent_label,
            section_label=candidate.section_label,
            action_label=candidate.label,
            description=candidate.description,
            target=candidate.selector,
            success=True,
            trigger_method="fill_and_submit" if submitted else "fill_only",
            network_requests=raw_network[before_network:],
            datalayer_events=datalayer,
            conversion_detected=conversion,
            notes="Form submitted" if submitted else "Fields filled; form not submitted",
        )

    @staticmethod
    def _blocked_result(candidate: InteractionCandidate, reason: str) -> InteractionResult:
        return InteractionResult(
            candidate_id=candidate.candidate_id,
            source_url=candidate.source_url,
            destination_url="",
            destination_page_type="",
            interaction_type=candidate.interaction_type,
            parent_label=candidate.parent_label,
            section_label=candidate.section_label,
            action_label=candidate.label,
            description=candidate.description,
            target=candidate.href or candidate.selector,
            success=False,
            trigger_method=candidate.trigger_method,
            notes=reason,
        )

    # ------------------------------------------------------------------
    # Runtime context helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_datalayer(page: Any) -> List[Any]:
        try:
            return page.evaluate(
                "() => Array.isArray(window.dataLayer) ? window.dataLayer.slice(-50) : []"
            ) or []
        except Exception:
            return []

    def _datalayer_has_conversion(self, rows: List[Any]) -> bool:
        try:
            return bool(self.CONVERSION_PATTERN.search(json.dumps(rows, default=str)))
        except Exception:
            return False

    def _classify_page_type(self, url: str, page: Any) -> str:
        text = url.lower()
        try:
            text += " " + (page.title() or "").lower()
            h1 = page.locator("h1").first
            if h1.count():
                text += " " + (h1.inner_text(timeout=300) or "").lower()
        except Exception:
            pass

        if self.CONVERSION_PATTERN.search(text):
            return "Conversion_or_ThankYou"
        if re.search(r"/cart|/basket", text):
            return "Cart"
        if re.search(r"/checkout|payment|shipping.?address", text):
            return "Checkout"
        if re.search(r"/product|/products|/p/|sku|model", text):
            return "Product"
        if re.search(r"/category|/collection|/catalog|/shop", text):
            return "Category"
        if re.search(r"contact|request|quote|demo", text):
            return "Lead_Generation"
        if re.search(r"support|help|knowledge|article", text):
            return "Support"
        if re.search(r"blog|news|resource|webinar|podcast|case.?study", text):
            return "Content"
        return "General"

    @staticmethod
    def _navigate_back(page: Any, source_url: str) -> None:
        try:
            page.goto(source_url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                page.wait_for_timeout(1500)
        except Exception:
            try:
                page.go_back(wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass

    @staticmethod
    def _sample_value(name: str, input_type: str) -> str:
        value = (name or "").lower()
        if "email" in value or input_type == "email":
            return "test@example.com"
        if "phone" in value or "mobile" in value or input_type == "tel":
            return "9999999999"
        if "company" in value or "organisation" in value or "organization" in value:
            return "Test Company"
        if "first" in value and "name" in value:
            return "Test"
        if "last" in value and "name" in value:
            return "User"
        if "name" in value:
            return "Test User"
        if "message" in value or "comment" in value or "description" in value:
            return "Automated analytics audit dry-run test."
        if input_type == "number":
            return "1"
        return "Test"
