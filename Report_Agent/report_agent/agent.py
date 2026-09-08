from pathlib import Path

import matplotlib

# Generate charts without opening a desktop window
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

from google.adk.agents.llm_agent import Agent
from google.adk.tools import ToolContext
from google.genai import types

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Image,
    Spacer,
    PageBreak,
    Table,
    TableStyle,
)


# =========================================================
# FILE PATHS
# =========================================================

AGENT_FOLDER = Path(__file__).resolve().parent

CSV_FILE = AGENT_FOLDER / "sample_sales.csv"

REGION_CHART_FILE = AGENT_FOLDER / "revenue_by_region.png"
TREND_CHART_FILE = AGENT_FOLDER / "revenue_trend.png"
PIE_CHART_FILE = AGENT_FOLDER / "revenue_share_pie.png"
PRODUCT_CHART_FILE = AGENT_FOLDER / "product_revenue.png"
PROFIT_CHART_FILE = AGENT_FOLDER / "profit_by_region.png"

PDF_FILE = AGENT_FOLDER / "sales_report.pdf"


# =========================================================
# HELPER FUNCTION
# =========================================================

def load_sales_data():
    """
    Load and validate the sales CSV file.

    This is an internal helper function and is not exposed
    directly as an ADK tool.
    """

    if not CSV_FILE.exists():
        return None, {
            "status": "error",
            "message": f"CSV file not found: {CSV_FILE}",
        }

    try:
        df = pd.read_csv(CSV_FILE)
    except Exception as error:
        return None, {
            "status": "error",
            "message": f"Unable to read the CSV file: {error}",
        }

    required_columns = [
        "Month",
        "Region",
        "Product",
        "Units",
        "Revenue",
        "Cost",
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing_columns:
        return None, {
            "status": "error",
            "message": "The CSV file is missing required columns.",
            "missing_columns": missing_columns,
        }

    numeric_columns = [
        "Units",
        "Revenue",
        "Cost",
    ]

    for column in numeric_columns:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    if df[numeric_columns].isna().any().any():
        return None, {
            "status": "error",
            "message": (
                "The Units, Revenue or Cost columns contain "
                "blank or invalid numeric values."
            ),
        }

    df["Profit"] = df["Revenue"] - df["Cost"]

    return df, None


# =========================================================
# TOOL 1: EXTRACT CSV
# =========================================================

def extract_csv():
    """
    Extract and validate records from sample_sales.csv.
    """

    df, error = load_sales_data()

    if error:
        return error

    return {
        "status": "success",
        "rows": int(len(df)),
        "columns": list(df.columns),
        "csv_file": str(CSV_FILE),
    }


# =========================================================
# TOOL 2: ANALYSE DATA
# =========================================================

def analyse_data():
    """
    Analyse CSV data and calculate sales and profit metrics.
    """

    df, error = load_sales_data()

    if error:
        return error

    total_revenue = float(df["Revenue"].sum())
    average_revenue = float(df["Revenue"].mean())
    total_cost = float(df["Cost"].sum())
    total_profit = float(df["Profit"].sum())
    total_units = int(df["Units"].sum())

    if total_revenue == 0:
        profit_margin = 0.0
    else:
        profit_margin = (
            total_profit / total_revenue
        ) * 100

    region_revenue = (
        df.groupby("Region")["Revenue"]
        .sum()
        .sort_values(ascending=False)
    )

    product_revenue = (
        df.groupby("Product")["Revenue"]
        .sum()
        .sort_values(ascending=False)
    )

    top_region = str(region_revenue.index[0])
    top_product = str(product_revenue.index[0])

    return {
        "status": "success",
        "rows_analysed": int(len(df)),
        "total_units": total_units,
        "total_revenue": round(total_revenue, 2),
        "average_revenue": round(average_revenue, 2),
        "total_cost": round(total_cost, 2),
        "total_profit": round(total_profit, 2),
        "profit_margin": round(profit_margin, 2),
        "top_region": top_region,
        "top_product": top_product,
    }


# =========================================================
# TOOL 3: REVENUE BY REGION BAR CHART
# =========================================================

def create_region_chart():
    """
    Create a bar chart showing total revenue by region.
    """

    df, error = load_sales_data()

    if error:
        return error

    region_summary = (
        df.groupby("Region")["Revenue"]
        .sum()
        .sort_values(ascending=False)
    )

    plt.figure(figsize=(8, 5))

    bars = plt.bar(
        region_summary.index,
        region_summary.values,
        color="#276EF1",
    )

    plt.title("Revenue by Region")
    plt.xlabel("Region")
    plt.ylabel("Revenue")
    plt.grid(
        axis="y",
        alpha=0.25,
    )

    for bar in bars:
        value = bar.get_height()

        plt.text(
            bar.get_x() + bar.get_width() / 2,
            value,
            f"${value:,.0f}",
            horizontalalignment="center",
            verticalalignment="bottom",
        )

    plt.tight_layout()

    plt.savefig(
        REGION_CHART_FILE,
        dpi=180,
        bbox_inches="tight",
    )

    plt.close()

    return {
        "status": "success",
        "chart_name": "Revenue by Region",
        "chart_file": str(REGION_CHART_FILE),
    }


# =========================================================
# TOOL 4: MONTHLY REVENUE TREND
# =========================================================

def create_trend_chart():
    """
    Create a line chart showing the monthly revenue trend.
    """

    df, error = load_sales_data()

    if error:
        return error

    month_order = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]

    monthly_revenue = (
        df.groupby("Month")["Revenue"]
        .sum()
        .reindex(month_order)
        .dropna()
    )

    plt.figure(figsize=(9, 5))

    plt.plot(
        monthly_revenue.index,
        monthly_revenue.values,
        marker="o",
        linewidth=2.5,
        color="#F59E0B",
    )

    plt.title("Monthly Revenue Trend")
    plt.xlabel("Month")
    plt.ylabel("Revenue")
    plt.grid(alpha=0.3)

    for month, value in zip(
        monthly_revenue.index,
        monthly_revenue.values,
    ):
        plt.annotate(
            f"${value:,.0f}",
            (month, value),
            textcoords="offset points",
            xytext=(0, 8),
            horizontalalignment="center",
        )

    plt.tight_layout()

    plt.savefig(
        TREND_CHART_FILE,
        dpi=180,
        bbox_inches="tight",
    )

    plt.close()

    return {
        "status": "success",
        "chart_name": "Monthly Revenue Trend",
        "chart_file": str(TREND_CHART_FILE),
    }


# =========================================================
# TOOL 5: REVENUE SHARE PIE CHART
# =========================================================

def create_pie_chart():
    """
    Create a pie chart showing revenue contribution by region.
    """

    df, error = load_sales_data()

    if error:
        return error

    region_revenue = (
        df.groupby("Region")["Revenue"]
        .sum()
        .sort_values(ascending=False)
    )

    chart_colors = [
        "#276EF1",
        "#22C55E",
        "#F59E0B",
        "#EF4444",
        "#8B5CF6",
    ]

    plt.figure(figsize=(7, 6))

    plt.pie(
        region_revenue.values,
        labels=region_revenue.index,
        autopct="%1.1f%%",
        startangle=90,
        colors=chart_colors[:len(region_revenue)],
        wedgeprops={
            "edgecolor": "white",
            "linewidth": 1,
        },
    )

    plt.title("Revenue Share by Region")
    plt.axis("equal")
    plt.tight_layout()

    plt.savefig(
        PIE_CHART_FILE,
        dpi=180,
        bbox_inches="tight",
    )

    plt.close()

    return {
        "status": "success",
        "chart_name": "Revenue Share by Region",
        "chart_file": str(PIE_CHART_FILE),
    }


# =========================================================
# TOOL 6: PRODUCT REVENUE CHART
# =========================================================

def create_product_chart():
    """
    Create a horizontal bar chart showing revenue by product.
    """

    df, error = load_sales_data()

    if error:
        return error

    product_revenue = (
        df.groupby("Product")["Revenue"]
        .sum()
        .sort_values(ascending=True)
    )

    plt.figure(figsize=(9, 5))

    bars = plt.barh(
        product_revenue.index,
        product_revenue.values,
        color="#22C55E",
    )

    plt.title("Revenue by Product")
    plt.xlabel("Revenue")
    plt.ylabel("Product")
    plt.grid(
        axis="x",
        alpha=0.25,
    )

    for bar in bars:
        value = bar.get_width()

        plt.text(
            value,
            bar.get_y() + bar.get_height() / 2,
            f" ${value:,.0f}",
            verticalalignment="center",
        )

    plt.tight_layout()

    plt.savefig(
        PRODUCT_CHART_FILE,
        dpi=180,
        bbox_inches="tight",
    )

    plt.close()

    return {
        "status": "success",
        "chart_name": "Revenue by Product",
        "chart_file": str(PRODUCT_CHART_FILE),
    }


# =========================================================
# TOOL 7: PROFIT BY REGION CHART
# =========================================================

def create_profit_chart():
    """
    Create a bar chart showing total profit by region.
    """

    df, error = load_sales_data()

    if error:
        return error

    profit_summary = (
        df.groupby("Region")["Profit"]
        .sum()
        .sort_values(ascending=False)
    )

    plt.figure(figsize=(8, 5))

    bars = plt.bar(
        profit_summary.index,
        profit_summary.values,
        color="#16A34A",
    )

    plt.title("Profit by Region")
    plt.xlabel("Region")
    plt.ylabel("Profit")
    plt.grid(
        axis="y",
        alpha=0.25,
    )

    for bar in bars:
        value = bar.get_height()

        plt.text(
            bar.get_x() + bar.get_width() / 2,
            value,
            f"${value:,.0f}",
            horizontalalignment="center",
            verticalalignment="bottom",
        )

    plt.tight_layout()

    plt.savefig(
        PROFIT_CHART_FILE,
        dpi=180,
        bbox_inches="tight",
    )

    plt.close()

    return {
        "status": "success",
        "chart_name": "Profit by Region",
        "chart_file": str(PROFIT_CHART_FILE),
    }


# =========================================================
# TOOL 8: CREATE ALL CHARTS
# =========================================================

def create_all_charts():
    """
    Generate all charts required for the sales report.
    """

    extraction_result = extract_csv()

    if extraction_result["status"] != "success":
        return extraction_result

    chart_results = [
        create_region_chart(),
        create_trend_chart(),
        create_pie_chart(),
        create_product_chart(),
        create_profit_chart(),
    ]

    failed_charts = [
        result
        for result in chart_results
        if result.get("status") != "success"
    ]

    if failed_charts:
        return {
            "status": "error",
            "message": "One or more charts could not be generated.",
            "errors": failed_charts,
        }

    return {
        "status": "success",
        "message": "All five charts were generated successfully.",
        "charts": chart_results,
    }


# =========================================================
# TOOL 9: GENERATE PDF AND SAVE AS ADK ARTIFACT
# =========================================================

async def generate_pdf(tool_context: ToolContext):
    """
    Generate the complete PDF report and save it as an ADK artifact.
    """

    extraction_result = extract_csv()

    if extraction_result["status"] != "success":
        return extraction_result

    metrics = analyse_data()

    if metrics["status"] != "success":
        return metrics

    charts_result = create_all_charts()

    if charts_result["status"] != "success":
        return charts_result

    styles = getSampleStyleSheet()

    pdf = SimpleDocTemplate(
        str(PDF_FILE),
        pagesize=A4,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40,
        title="Sales Analysis Report",
        author="Document Report Generation Agent",
    )

    content = []

    # PDF title
    content.append(
        Paragraph(
            "Sales Analysis Report",
            styles["Title"],
        )
    )

    content.append(Spacer(1, 12))

    content.append(
        Paragraph(
            (
                "Generated by the Google ADK Document "
                "and Report Generation Agent"
            ),
            styles["BodyText"],
        )
    )

    content.append(Spacer(1, 20))

    # Executive summary
    content.append(
        Paragraph(
            "Executive Summary",
            styles["Heading2"],
        )
    )

    summary_text = (
        f"The analysis covers {metrics['rows_analysed']} records "
        f"and {metrics['total_units']} total units. "
        f"Total revenue was ${metrics['total_revenue']:,.2f}, "
        f"with total profit of ${metrics['total_profit']:,.2f}. "
        f"The calculated profit margin was "
        f"{metrics['profit_margin']:.2f}%. "
        f"The highest-revenue region was "
        f"{metrics['top_region']}, and the highest-revenue "
        f"product was {metrics['top_product']}."
    )

    content.append(
        Paragraph(
            summary_text,
            styles["BodyText"],
        )
    )

    content.append(Spacer(1, 20))

    # Summary metrics
    content.append(
        Paragraph(
            "Summary Metrics",
            styles["Heading2"],
        )
    )

    metrics_table_data = [
        ["Metric", "Value"],
        [
            "Records analysed",
            f"{metrics['rows_analysed']:,}",
        ],
        [
            "Total units",
            f"{metrics['total_units']:,}",
        ],
        [
            "Total revenue",
            f"${metrics['total_revenue']:,.2f}",
        ],
        [
            "Average revenue",
            f"${metrics['average_revenue']:,.2f}",
        ],
        [
            "Total cost",
            f"${metrics['total_cost']:,.2f}",
        ],
        [
            "Total profit",
            f"${metrics['total_profit']:,.2f}",
        ],
        [
            "Profit margin",
            f"{metrics['profit_margin']:.2f}%",
        ],
        [
            "Top region",
            metrics["top_region"],
        ],
        [
            "Top product",
            metrics["top_product"],
        ],
    ]

    metrics_table = Table(
        metrics_table_data,
        colWidths=[
            3.2 * inch,
            2.8 * inch,
        ],
        repeatRows=1,
    )

    metrics_table.setStyle(
        TableStyle(
            [
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor("#174EA6"),
                ),
                (
                    "TEXTCOLOR",
                    (0, 0),
                    (-1, 0),
                    colors.white,
                ),
                (
                    "FONTNAME",
                    (0, 0),
                    (-1, 0),
                    "Helvetica-Bold",
                ),
                (
                    "BACKGROUND",
                    (0, 1),
                    (-1, -1),
                    colors.HexColor("#F4F7FB"),
                ),
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.5,
                    colors.HexColor("#C9D2E3"),
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "MIDDLE",
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    8,
                ),
                (
                    "RIGHTPADDING",
                    (0, 0),
                    (-1, -1),
                    8,
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    7,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    7,
                ),
            ]
        )
    )

    content.append(metrics_table)
    content.append(PageBreak())

    # Revenue by region
    content.append(
        Paragraph(
            "Revenue by Region",
            styles["Heading2"],
        )
    )

    content.append(Spacer(1, 10))

    content.append(
        Image(
            str(REGION_CHART_FILE),
            width=6.5 * inch,
            height=4.0 * inch,
        )
    )

    content.append(PageBreak())

    # Revenue share
    content.append(
        Paragraph(
            "Revenue Share by Region",
            styles["Heading2"],
        )
    )

    content.append(Spacer(1, 10))

    content.append(
        Image(
            str(PIE_CHART_FILE),
            width=5.8 * inch,
            height=4.5 * inch,
        )
    )

    content.append(PageBreak())

    # Monthly revenue trend
    content.append(
        Paragraph(
            "Monthly Revenue Trend",
            styles["Heading2"],
        )
    )

    content.append(Spacer(1, 10))

    content.append(
        Image(
            str(TREND_CHART_FILE),
            width=6.5 * inch,
            height=4.0 * inch,
        )
    )

    content.append(PageBreak())

    # Product revenue
    content.append(
        Paragraph(
            "Revenue by Product",
            styles["Heading2"],
        )
    )

    content.append(Spacer(1, 10))

    content.append(
        Image(
            str(PRODUCT_CHART_FILE),
            width=6.5 * inch,
            height=4.0 * inch,
        )
    )

    content.append(PageBreak())

    # Profit by region
    content.append(
        Paragraph(
            "Profit by Region",
            styles["Heading2"],
        )
    )

    content.append(Spacer(1, 10))

    content.append(
        Image(
            str(PROFIT_CHART_FILE),
            width=6.5 * inch,
            height=4.0 * inch,
        )
    )

    # Build the PDF
    pdf.build(content)

    # Read the generated PDF as binary data
    with open(PDF_FILE, "rb") as file:
        pdf_bytes = file.read()

    # Convert the PDF into an ADK-compatible artifact
    pdf_artifact = types.Part.from_bytes(
        data=pdf_bytes,
        mime_type="application/pdf",
    )

    # Save using ADK Artifacts
    artifact_version = await tool_context.save_artifact(
        filename=PDF_FILE.name,
        artifact=pdf_artifact,
    )

    return {
        "status": "success",
        "message": (
            "The PDF report was generated and saved "
            "successfully as an ADK artifact."
        ),
        "report_file": str(PDF_FILE),
        "artifact_filename": PDF_FILE.name,
        "artifact_version": artifact_version,
        "metrics": metrics,
        "charts": [
            REGION_CHART_FILE.name,
            TREND_CHART_FILE.name,
            PIE_CHART_FILE.name,
            PRODUCT_CHART_FILE.name,
            PROFIT_CHART_FILE.name,
        ],
    }


# =========================================================
# ROOT ADK AGENT
# =========================================================

root_agent = Agent(
    model="gemini-3.5-flash",
    name="report_agent",
    description=(
        "Extracts CSV data, performs pandas analysis, creates "
        "multiple charts, generates a PDF report and saves "
        "the report through the ADK Artifacts service."
    ),
    instruction="""
You are a document and report generation agent.

Follow these rules:

1. Use extract_csv when the user asks to read, inspect or
   validate sample_sales.csv.

2. Use analyse_data when the user asks for revenue, cost,
   profit, margin, product or regional metrics.

3. Use create_region_chart when the user asks for the
   revenue-by-region bar chart.

4. Use create_trend_chart when the user asks for the monthly
   revenue trend chart.

5. Use create_pie_chart when the user asks for the regional
   revenue-share pie chart.

6. Use create_product_chart when the user asks for the
   product-revenue chart.

7. Use create_profit_chart when the user asks for the
   profit-by-region chart.

8. Use create_all_charts when the user asks to generate
   every chart.

9. Use generate_pdf when the user asks for the complete PDF
   report. The report must include the summary metrics and
   all charts and must be saved using ADK Artifacts.

Never invent values. Use only values calculated by the tools.

After generating the PDF, tell the user the artifact filename
and artifact version.
""",
    tools=[
        extract_csv,
        analyse_data,
        create_region_chart,
        create_trend_chart,
        create_pie_chart,
        create_product_chart,
        create_profit_chart,
        create_all_charts,
        generate_pdf,
    ],
)