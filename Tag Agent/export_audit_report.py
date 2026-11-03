import argparse
import csv
import json
from pathlib import Path

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
except ImportError:
    Workbook = None


def clean_value(value):
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        rows = [{"message": "No data"}]
    headers = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: clean_value(row.get(h, "")) for h in headers})


def report_to_rows(report):
    summary = [{
        "url": report.get("url", ""),
        "domain": report.get("domain", ""),
        "audited_at": report.get("audited_at", ""),
        "page_title": report.get("page_title", ""),
        "health_score": report.get("health_score", ""),
        "spa_detected": report.get("spa_detected", ""),
        "consent_platform": report.get("consent_layer", {}).get("platform", ""),
        "issues_count": len(report.get("issues", [])),
        "recommendations_count": len(report.get("recommendations", [])),
    }]

    tags = []
    for tag in report.get("tags", []):
        tags.append({
            "name": tag.get("name", ""),
            "vendor": tag.get("vendor", ""),
            "category": tag.get("category", ""),
            "priority": tag.get("priority", ""),
            "status": tag.get("status", ""),
            "score": tag.get("score", ""),
            "gdpr_risk": tag.get("gdpr_risk", ""),
            "matched_signals": " | ".join(tag.get("matched_signals", [])),
            "param_issues": " | ".join(tag.get("param_issues", [])),
            "firing_issues": " | ".join(tag.get("firing_issues", [])),
            "network_requests": " | ".join(tag.get("network_requests", [])),
        })

    issues = []
    for issue in report.get("issues", []):
        issues.append({
            "severity": issue.get("severity", ""),
            "category": issue.get("category", ""),
            "tag": issue.get("tag", ""),
            "issue": issue.get("issue", ""),
            "recommendation": issue.get("recommendation", ""),
            "evidence": issue.get("evidence", ""),
        })

    recommendations = []
    for i, rec in enumerate(report.get("recommendations", []), start=1):
        recommendations.append({
            "priority_no": i,
            "recommendation": rec,
        })

    custom_events = []
    for event in report.get("custom_events", []):
        custom_events.append({"event_name": event})

    specialist = []
    sections = [
        "consent_layer", "datalayer_health", "spa_audit", "cross_domain_tracking",
        "ecommerce_audit", "form_tracking", "scroll_tracking", "video_tracking",
        "audio_tracking", "nav_tracking", "localisation", "conditional_firing",
        "performance_impact", "network_summary",
    ]
    for section in sections:
        data = report.get(section, {})
        if isinstance(data, dict):
            issues_list = data.get("issues", [])
            specialist.append({
                "section": section,
                "score": data.get("score", ""),
                "detected": data.get("detected", data.get("tracking_detected", "")),
                "issues": " | ".join(issues_list) if isinstance(issues_list, list) else clean_value(issues_list),
                "details_json": json.dumps(data, ensure_ascii=False),
            })

    return {
        "summary": summary,
        "tags": tags,
        "issues": issues,
        "recommendations": recommendations,
        "custom_events": custom_events,
        "specialist_audits": specialist,
    }


def export_excel(output_path, datasets):
    if Workbook is None:
        raise Exception("openpyxl is not installed. Run: python -m pip install openpyxl")

    wb = Workbook()
    default_ws = wb.active
    wb.remove(default_ws)

    header_fill = PatternFill(fill_type="solid", fgColor="1F4E78")
    header_font = Font(bold=True, color="FFFFFF")

    for sheet_name, rows in datasets.items():
        ws = wb.create_sheet(sheet_name[:31])
        if not rows:
            rows = [{"message": "No data"}]

        headers = list(rows[0].keys())
        ws.append(headers)

        for row in rows:
            ws.append([clean_value(row.get(h, "")) for h in headers])

        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)

        for column_cells in ws.columns:
            max_length = 0
            column_letter = column_cells[0].column_letter
            for cell in column_cells:
                value = clean_value(cell.value)
                max_length = max(max_length, len(value))
            ws.column_dimensions[column_letter].width = min(max(max_length + 2, 12), 60)

        ws.freeze_panes = "A2"

    wb.save(output_path)


def main():
    parser = argparse.ArgumentParser(description="Convert Martech Tag Audit JSON reports to CSV and Excel.")
    parser.add_argument("--folder", required=True, help="Folder containing JSON audit reports")
    parser.add_argument("--csv", action="store_true", help="Create CSV files")
    parser.add_argument("--excel", action="store_true", help="Create Excel file")
    parser.add_argument("--output-dir", default="audit_exports", help="Output folder")
    args = parser.parse_args()

    reports_folder = Path(args.folder)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)

    json_files = list(reports_folder.glob("*.json"))
    if not json_files:
        print("No JSON reports found.")
        return

    if not args.csv and not args.excel:
        print("Please add --csv and/or --excel")
        return

    for json_file in json_files:
        with open(json_file, "r", encoding="utf-8") as f:
            report = json.load(f)

        domain = report.get("domain", json_file.stem).replace(".", "_")
        datasets = report_to_rows(report)

        if args.csv:
            for name, rows in datasets.items():
                csv_path = output_dir / f"{domain}_{name}.csv"
                write_csv(csv_path, rows)
                print(f"CSV created: {csv_path}")

        if args.excel:
            excel_path = output_dir / f"{domain}_audit_report.xlsx"
            export_excel(excel_path, datasets)
            print(f"Excel created: {excel_path}")


if __name__ == "__main__":
    main()
