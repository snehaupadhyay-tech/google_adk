from google.adk.agents import Agent, LoopAgent
from google.adk.tools import ToolContext

MODEL = "gemini-3.5-flash-lite"

def exit_loop(tool_context: ToolContext):
    "Call this once the story has proper ending"
    tool_context.actions.escalate = True
    #stop handling this level and give control to parent
    return {}


story_agent = Agent(
    name = 'story_agent',
    model = MODEL,
    instruction= ("Continue the story by adding short new sentence"
                  "Story so far: "
                  "output is the full story so far,including the new sentence"),
    output_key= 'story'
)

checker_agent = Agent(
    name = 'checker_agent',
    model = MODEL,
    instruction=(
    "Read the story so far: {story}. "
    "Do not call exit_loop until the story contains at least 10 sentences. "
    "If there are fewer than 10 sentences, respond with continue."
     ),
    tools= [exit_loop]
)


root_agent = LoopAgent(
    name = "Loop_demo",
    sub_agents = [story_agent,checker_agent],
    max_iterations= 10,
    description ="Repeat story_agent -> checker_agent stopping early via exit_loop or after 10 iterations "
    )
