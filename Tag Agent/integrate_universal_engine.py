#!/usr/bin/env python3
"""Safely integrate universal_interaction_engine.py into martech_tag_auditor.py.

Run from PowerShell:
  python integrate_universal_engine.py --auditor "C:\\...\\martech_tag_auditor.py" \
      --engine "C:\\...\\universal_interaction_engine.py"

The script:
- creates a timestamped backup;
- validates both source files before modifying anything;
- adds UniversalInteractionEngine imports;
- initialises the engine in JourneyAuditor;
- adds result conversion into existing JourneyEvent/GA4/Adobe/network models;
- replaces the old generic click loop and legacy form audit with universal discovery;
- adds universal metadata columns to Journey_Audit;
- adds a Universal_Interactions worksheet;
- compiles the integrated auditor and automatically restores the backup on error.
"""
from __future__ import annotations

import argparse
import py_compile
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

IMPORT_BLOCK = '''from universal_interaction_engine import (
    InteractionCandidate,
    InteractionResult,
    UniversalInteractionEngine,
)
'''

ENGINE_INIT = '''
        # Universal, behaviour-driven website interaction discovery.
        # No website menu labels or domain-specific journeys are hard-coded.
        self.universal_engine = UniversalInteractionEngine(
            max_candidates=max(150, max_clicks_per_page * 5),
            max_actions=max_clicks_per_page,
            menu_wait_ms=1000,
            action_wait_ms=3500,
            allow_add_to_cart=False,
            submit_forms=submit_forms,
            allow_external_navigation=False,
        )
'''

CONVERTER_METHOD = r'''
    def _convert_universal_results(
        self,
        results: List[InteractionResult],
    ) -> List[JourneyEvent]:
        """Convert universal journey results into the auditor's native event model."""
        journey_events: List[JourneyEvent] = []

        for result in results:
            raw_requests = result.network_requests or []
            request_urls = [
                request.get("url", "")
                for request in raw_requests
                if isinstance(request, dict)
            ]
            ga4_events = [
                parse_ga4_request(request_url)
                for request_url in request_urls
                if is_ga4_request(request_url)
            ]
            adobe_events = [
                parse_adobe_request(request_url)
                for request_url in request_urls
                if is_adobe_request(request_url)
            ]
            destination_url = result.destination_url or result.source_url
            network_logs = filter_martech_logs(
                raw_requests,
                destination_url,
                result.action_label,
            )

            note_parts: List[str] = []
            if result.notes:
                note_parts.append(result.notes)
            if result.source_url:
                note_parts.append(f"Source Page: {result.source_url}")
            if result.parent_label:
                note_parts.append(f"Parent Navigation: {result.parent_label}")
            if result.section_label:
                note_parts.append(f"Navigation Section: {result.section_label}")
            if result.destination_page_type:
                note_parts.append(
                    f"Destination Page Type: {result.destination_page_type}"
                )
            if result.trigger_method:
                note_parts.append(f"Trigger Method: {result.trigger_method}")
            if result.conversion_detected:
                note_parts.append("Conversion or Thank-You signal detected")

            journey_events.append(
                JourneyEvent(
                    url=destination_url,
                    action_type=result.interaction_type,
                    action_label=result.action_label,
                    target=result.target,
                    success=result.success,
                    ga4_events=ga4_events,
                    adobe_events=adobe_events,
                    datalayer_events=result.datalayer_events or [],
                    network_requests=request_urls[:150],
                    network_logs=network_logs,
                    notes=" | ".join(note_parts),
                )
            )

        return journey_events

'''

UNIVERSAL_EXECUTION = '''                # ---- universal interaction discovery and execution ----
                # This replaces the old DOM-order click loop and legacy form audit.
                # Menus are discovered by before/after DOM visibility changes.
                universal_candidates = self.universal_engine.discover(page)
                candidate_counts = Counter(
                    candidate.interaction_type
                    for candidate in universal_candidates
                )
                print(
                    f"  [UNIVERSAL] Discovered {len(universal_candidates)} "
                    f"unique candidates on {page.url}"
                )
                for interaction_type, count in candidate_counts.most_common():
                    print(f"    {interaction_type}: {count}")

                universal_results = self.universal_engine.execute(
                    page=page,
                    candidates=universal_candidates,
                    raw_network=raw_network,
                )
                events.extend(
                    self._convert_universal_results(universal_results)
                )

                successful_universal = sum(
                    1 for result in universal_results if result.success
                )
                conversion_count = sum(
                    1 for result in universal_results
                    if result.conversion_detected
                )
                print(
                    f"  [UNIVERSAL] Executed {len(universal_results)} interactions; "
                    f"{successful_universal} successful; "
                    f"{conversion_count} conversion signal(s)."
                )

                if not page.is_closed() and page.url != url:
                    self._navigate(page, url)
                    self._handle_cookie_banner(page)

'''

NOTE_HELPER = r'''
def extract_note_value(notes: str, field_name: str) -> str:
    """Extract a pipe-delimited metadata value from JourneyEvent.notes."""
    if not notes:
        return ""
    pattern = re.compile(
        rf"(?:^|\|\s*){re.escape(field_name)}:\s*([^|]+)",
        re.I,
    )
    match = pattern.search(notes)
    return match.group(1).strip() if match else ""


'''

UNIVERSAL_ROWS = '''        universal_action_types = {
            "Expandable_Navigation_Link", "Product_Card",
            "Industry_or_Segment_Card", "Resource_Card", "Content_Card",
            "Lead_Generation_CTA", "Add_To_Cart", "Tab_Control",
            "Accordion_Control", "Carousel_Control", "Search_Filter_Control",
            "Download_Link", "Media_Link", "Form_Dry_Run", "Form_Submit",
            "External_Link", "Auth_Gated", "Conversion", "Visible_Link_or_CTA",
        }
        universal_rows = []
        for event in audit.journey_events:
            if event.action_type not in universal_action_types:
                continue
            universal_rows.append({
                "Source Page": extract_note_value(event.notes, "Source Page"),
                "Destination URL": event.url,
                "Destination Page Type": extract_note_value(
                    event.notes, "Destination Page Type"
                ),
                "Interaction Type": event.action_type,
                "Parent Navigation": extract_note_value(
                    event.notes, "Parent Navigation"
                ),
                "Navigation Section": extract_note_value(
                    event.notes, "Navigation Section"
                ),
                "Action Label": event.action_label,
                "Target": event.target,
                "Success": event.success,
                "Conversion Detected": (
                    "Conversion or Thank-You signal detected" in event.notes
                ),
                "GA4 Hits": len(event.ga4_events),
                "Adobe Hits": len(event.adobe_events),
                "DataLayer Events": len(event.datalayer_events),
                "Network Logs": len(event.network_logs),
                "Notes": event.notes,
            })

'''


def compile_file(path: Path) -> None:
    py_compile.compile(str(path), doraise=True)


def insert_import(text: str) -> str:
    if "from universal_interaction_engine import" in text:
        return text
    marker = "import pandas as pd\n"
    if marker not in text:
        raise RuntimeError("Could not find 'import pandas as pd'.")
    return text.replace(marker, marker + "\n" + IMPORT_BLOCK, 1)


def insert_engine_init(text: str) -> str:
    if "self.universal_engine = UniversalInteractionEngine(" in text:
        return text
    marker = "        self.max_tracked_per_type = max_tracked_per_type\n"
    if marker not in text:
        raise RuntimeError("Could not find JourneyAuditor max_tracked_per_type assignment.")
    return text.replace(marker, marker + ENGINE_INIT, 1)


def insert_converter(text: str) -> str:
    if "def _convert_universal_results(" in text:
        return text
    marker = "    @staticmethod\n    def _make_event("
    if marker not in text:
        raise RuntimeError("Could not find JourneyAuditor._make_event.")
    return text.replace(marker, CONVERTER_METHOD + marker, 1)


def replace_click_and_form_blocks(text: str) -> str:
    if "# ---- universal interaction discovery and execution ----" in text:
        return text
    start_marker = "                # ---- click interactions (safe/visible CTAs) ----\n"
    if start_marker not in text:
        # Backwards-compatible marker used by previous source version.
        start_marker = "                # ---- click interactions ----\n"
    end_marker = "                browser.close()\n"
    start = text.find(start_marker)
    end = text.find(end_marker, start)
    if start < 0 or end < 0:
        raise RuntimeError("Could not locate old click/form audit block.")
    return text[:start] + UNIVERSAL_EXECUTION + text[end:]


def insert_note_helper(text: str) -> str:
    if "def extract_note_value(" in text:
        return text
    marker = "def ensure_scheme(url: str) -> str:\n"
    if marker not in text:
        raise RuntimeError("Could not find utility helper section.")
    return text.replace(marker, NOTE_HELPER + marker, 1)


def extend_journey_rows(text: str) -> str:
    if '"Parent Navigation": extract_note_value(' in text:
        return text
    old = '''                "URL": event.url, "Action Type": event.action_type,
                "Action Label": event.action_label, "Target": event.target,
'''
    new = '''                "URL": event.url,
                "Source Page": extract_note_value(event.notes, "Source Page"),
                "Destination Page Type": extract_note_value(event.notes, "Destination Page Type"),
                "Parent Navigation": extract_note_value(event.notes, "Parent Navigation"),
                "Navigation Section": extract_note_value(event.notes, "Navigation Section"),
                "Conversion Detected": "Conversion or Thank-You signal detected" in event.notes,
                "Action Type": event.action_type,
                "Action Label": event.action_label, "Target": event.target,
'''
    if old not in text:
        raise RuntimeError("Could not find Journey_Audit row structure.")
    return text.replace(old, new, 1)


def extend_journey_columns(text: str) -> str:
    old = 'journey_columns = ["URL", "Action Type", "Action Label", "Target", "Success", "GA4 Hit Count", "Adobe Hit Count", "DataLayer Recent Count", "Network Log Count", "Total Network Count", "Notes"]'
    new = '''journey_columns = [
            "URL", "Source Page", "Destination Page Type", "Action Type",
            "Parent Navigation", "Navigation Section", "Action Label", "Target",
            "Success", "Conversion Detected", "GA4 Hit Count", "Adobe Hit Count",
            "DataLayer Recent Count", "Network Log Count", "Total Network Count", "Notes"
        ]'''
    if old not in text:
        if '"Parent Navigation", "Navigation Section"' in text:
            return text
        raise RuntimeError("Could not find journey_columns definition.")
    return text.replace(old, new, 1)


def insert_universal_rows(text: str) -> str:
    if "universal_action_types = {" in text:
        return text
    marker = "        with pd.ExcelWriter(excel_path, engine=\"openpyxl\") as writer:\n"
    if marker not in text:
        raise RuntimeError("Could not find Excel writer block.")
    return text.replace(marker, UNIVERSAL_ROWS + marker, 1)


def insert_universal_sheet(text: str) -> str:
    if 'sheet_name="Universal_Interactions"' in text:
        return text
    marker = '''            pd.DataFrame(journey_rows, columns=journey_columns).to_excel(writer, sheet_name="Journey_Audit", index=False)
'''
    addition = marker + '''            pd.DataFrame(universal_rows).to_excel(writer, sheet_name="Universal_Interactions", index=False)
'''
    if marker not in text:
        raise RuntimeError("Could not find Journey_Audit Excel export.")
    return text.replace(marker, addition, 1)


def validate_unescaped_source(text: str) -> None:
    escaped = ["-&gt;", "=&gt;", "&gt;", "&lt;", "&amp;&amp;"]
    found = [item for item in escaped if item in text]
    if found:
        raise RuntimeError(
            "The auditor file contains HTML-escaped operators: " + ", ".join(found) +
            ". Use the actual .py file from VS Code, not text copied from chat."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Integrate universal journey discovery into martech tag auditor")
    parser.add_argument("--auditor", required=True, help="Path to martech_tag_auditor.py")
    parser.add_argument("--engine", required=True, help="Path to universal_interaction_engine.py")
    args = parser.parse_args()

    auditor = Path(args.auditor).expanduser().resolve()
    engine = Path(args.engine).expanduser().resolve()
    if not auditor.exists():
        raise FileNotFoundError(f"Auditor not found: {auditor}")
    if not engine.exists():
        raise FileNotFoundError(f"Universal engine not found: {engine}")

    compile_file(auditor)
    compile_file(engine)
    original = auditor.read_text(encoding="utf-8")
    validate_unescaped_source(original)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = auditor.with_name(f"{auditor.stem}_backup_{stamp}{auditor.suffix}")
    shutil.copy2(auditor, backup)

    try:
        updated = original
        updated = insert_import(updated)
        updated = insert_engine_init(updated)
        updated = insert_converter(updated)
        updated = replace_click_and_form_blocks(updated)
        updated = insert_note_helper(updated)
        updated = extend_journey_rows(updated)
        updated = extend_journey_columns(updated)
        updated = insert_universal_rows(updated)
        updated = insert_universal_sheet(updated)
        auditor.write_text(updated, encoding="utf-8", newline="\n")
        compile_file(auditor)
    except Exception:
        shutil.copy2(backup, auditor)
        raise

    print("[OK] Universal journey engine integrated successfully.")
    print(f"[OK] Updated auditor: {auditor}")
    print(f"[OK] Backup created: {backup}")
    print(f"[OK] Universal engine: {engine}")
    print("[OK] Both Python files compile successfully.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
