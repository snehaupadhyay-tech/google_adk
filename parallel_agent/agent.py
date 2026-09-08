from google.adk.agents import Agent, ParallelAgent

MODEL = "gemini-3.5-flash-lite"

pros_agent = Agent(
    name = 'pros_agent',
    model = MODEL,
    instruction= ("The user will give you the topic.Write exactly one clear benefit"
                  "output is only sentence"),
    output_key= 'pros'
)

cons_agent = Agent(
    name = 'cons_agent',
    model = MODEL,
    instruction= ("The user will give you the topic.Write exactly one clear drawback"
                  "output is only sentence"),
    output_key= 'cons'
)


root_agent = ParallelAgent(
    name = "Parellel_demo",
    sub_agents = [pros_agent,cons_agent],
    description ="Runs pros_agent and cons_agent at same time, each saving its own time"
    )
