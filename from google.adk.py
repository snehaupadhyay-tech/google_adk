from google.adk.agents import Agent, LoopAgent
from google.adk.tools import ToolContext
 
MODEL = "gemini-3.5-flash-lite"
 
story = " "
 
def exit_loop(tool_context:ToolContext):
    "Call this once the story has proper ending"
    tool_context.actions.escalate = True
    # stop handling at this level and give back control to the parent.
    return {}
 
story_agent = Agent(
    name = "story_agent",
    model = MODEL,
    instruction = (
        "Continue the story by adding exactly one short new sentence"
        "Story so far"
        "Output the full story so far, including the new sentence"
    ),
    output_key="story"
)
 
checker_agent = Agent(
    name = "checker_agent",
    model = MODEL,
    instruction = (
        "Read the story so far: {story}"
        "If the story now has a clear ending call the exit_loop tool"
        "Otherwise do nothing and just reply with 'continue'."
    ),
    tools = [exit_loop]
)
 
root_agent = LoopAgent(
    name = 'loop_demo',
    sub_agents = [story_agent, checker_agent],
    max_iterations= 10,
    description = 'Repeats story_agent -> checker_agent, stopping early via exit loop or after 10 iterations'
 
)