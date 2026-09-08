from dotenv import load_dotenv
from google import genai
 
load_dotenv()
 
client = genai.Client()
 
question = input("Ask a question:")
 
response = client.models.generate_content(
    model = "gemini-3.5-flash-lite",
    contents=question
)
 
print(response.text)