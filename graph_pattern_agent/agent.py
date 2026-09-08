from google.adk import Agent, Workflow
 
def finance_handler(node_input:str):
    return f"[FINANCE AGENT] Handling finance query: {node_input}"
 
def hr_handler(node_input:str):
    return f"[HR AGENT] Handing HR query: {node_input}"
 
def general_handler(node_input:str):
    return f"[GENERAL AGENT] Handing generic query: {node_input}"
 
router = Agent(
    name = 'router',
    model = 'gemini-3.5-flash-lite',
    instruction = """
Classify the request.
Return only one word from this list: [finance,hr,general]"""
)
 
root_agent = Workflow(
    name = 'routing_demo',
    edges = [
        ('START',
         router),
         (
             router,
             {
                 'finance':finance_handler,
                 'hr': hr_handler,
                 'general': general_handler
             }
         )
    ]
)