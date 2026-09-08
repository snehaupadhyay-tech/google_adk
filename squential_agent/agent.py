from google.adk.agents import Agent, SequentialAgent

MODEL = "gemini-3.5-flash-lite"

writer = Agent(
    name = 'writer',
    model = MODEL,
    instruction= ("The user will give you the topic.Write exactly a short sentence about it"
                  "output is only sentence"),
    output_key= 'draft'
)

editor = Agent(
    name = 'editor',
    model = MODEL,
    instruction= ("Rewrite the sentence to make it more engaging"
                  "Keep it one sentence only and output only the rewritten sentence"
                  "Sentence: {draft}"),
     output_key= 'final'
)

root_agent = SequentialAgent(
    name = "sequential_demo",
    sub_agents = [writer,editor],
    description ="Runs writer then editor in strict order passing state between them"
    )
