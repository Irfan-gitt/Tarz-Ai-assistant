from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
import os

load_dotenv()
api_openai = os.getenv("GITHUB_TOKEN")
llm = ChatOpenAI(
    base_url="https://models.inference.ai.azure.com",
    api_key=api_openai,
    model="gpt-4o-mini"
)

print(llm.invoke("say hello").content)
