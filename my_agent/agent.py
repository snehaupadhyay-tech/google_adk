from google.adk.agents.llm_agent import Agent

root_agent = Agent(
    model='gemini-3.5-flash-lite',
    name='root_agent',
    description='A helpful assistant for user questions.',
    instruction='Answer user questions to the best of your knowledge',
)

'''
Cost per million input token is 0.75 dollars
Cost per million output token is 3.75 dollars
Write a python function that taken input and output token counts as user input and calculates
the price incurred and it also calculates that how many such queries can be done for 1 dollar
 
'''
def estimate_questions_per_dollar(
    input_tokens: int,
    output_tokens: int,
) -> dict:
    """
    Estimates the cost of one interaction and calculates
    how many similar interactions can be performed for $1.
 
    Assumptions:
    - Input cost = $0.75 per 1M tokens
    - Output cost = $3.75 per 1M tokens
    - 1 token ~= 0.75 words (for information only)
    """
 
    INPUT_COST_PER_MILLION = 0.75
    OUTPUT_COST_PER_MILLION = 3.75
 
    input_cost = (input_tokens / 1000000) * INPUT_COST_PER_MILLION
    output_cost = (output_tokens / 1000000) * OUTPUT_COST_PER_MILLION
 
    total_cost = input_cost + output_cost
 
    questions_per_dollar = (
        round(1 / total_cost, 2) if total_cost > 0 else float("inf")
    )
 
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_input_words": round(input_tokens * 0.75),
        "estimated_output_words": round(output_tokens * 0.75),
        "cost_per_question_usd": round(total_cost, 8),
        "questions_per_dollar": questions_per_dollar,
    }

from google.adk.agents import Agent
 
root_agent = Agent(
    model="gemini-3.5-flash-lite",
    name="Cost_Estimation_Agent",
    description="Provides a cost estimation based on the input/output token shared by user",
    instruction=(
        "Use your knowledge to answer general queries but if a user asks about cost estimation,\
            take input and output token info from the user and invoke the tool to provide the estimate"
    ),
    tools=[estimate_questions_per_dollar],
)