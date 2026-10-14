from google.adk import Agent, Workflow, Runner, Event
from google.adk.events.event_actions import EventActions
 
router = Agent(
    name="router_agent",
    model="gemini-3.5-flash-lite",
    instruction="""
    Classify the request and return ONLY one of:
    finance
    hr
    general
    """
)
 
def triage(node_input):
    text = str(node_input).strip().lower()
    if 'finance' in text:
        route = 'finance'
    elif 'hr' in text:
        route = 'hr'
    else:
        route = 'general'
    return Event(output=text, actions=EventActions(route=route))
 
finance_agent = Agent(
    name="finance_agent",
    model="gemini-3.5-flash-lite",
    instruction="Always respond with: Your request is related to finance."
)
 
hr_agent = Agent(
    name="hr_agent",
    model="gemini-3.5-flash-lite",
    instruction="Always respond with: Your request is related to HR."
)
 
general_agent = Agent(
    name="general_agent",
    model="gemini-3.5-flash-lite",
    instruction="Always respond with: Your request is related to general."
)
 
root_agent = Workflow(
    name="routing_demo",
    edges=[
        ("START", router, triage),
        (
            triage,
            {
                "finance": finance_agent,
                "hr": hr_agent,
                "general": general_agent,
            }
        )
    ],
)
 
