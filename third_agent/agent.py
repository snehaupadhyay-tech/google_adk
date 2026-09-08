import io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
 
from google.genai import types
from google.adk.agents import Agent
from google.adk.tools import ToolContext
 
 
async def plot_bar_graph(
    labels: list[str],
    values: list[float],
    title: str,
    tool_context: ToolContext,
) -> dict:
    """
    Plots a bar graph from the given labels and values and saves it
    as an artifact so it can be displayed to the user.
 
    Args:
        labels: Category names for the x-axis (e.g. ["Jan", "Feb", "Mar"]).
        values: Numeric values for each label (e.g. [10, 25, 15]).
        title: Title to display on the chart.
    """
 
    fig, ax = plt.subplots()
    ax.bar(labels, values)
    ax.set_title(title)
    ax.set_ylabel("Value")
 
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
 
    image_part = types.Part.from_bytes(
        data=buf.getvalue(),
        mime_type="image/png",
    )
 
    filename = f"{title.replace(' ', '_').lower()}_bargraph.png"
    version = await tool_context.save_artifact(filename, image_part)
 
    return {
        "status": "success",
        "artifact_filename": filename,
        "artifact_version": version,
        "message": f"Bar graph '{title}' generated and saved as an artifact.",
    }
 
 
root_agent = Agent(
    model="gemini-3.5-flash-lite",
    name="Bar_Graph_Agent",
    description="Plots a bar graph from user-provided labels and values",
    instruction=(
        "Use your knowledge to answer general queries, but if a user gives you "
        "categorical data (labels and numeric values) and asks for a chart, bar "
        "graph, or visualization, invoke the tool to generate and save it as an "
        "artifact."
    ),
    tools=[plot_bar_graph],
)