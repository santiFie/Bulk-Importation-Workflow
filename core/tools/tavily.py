from langchain_tavily import TavilySearch

def generate_tavily():
    return TavilySearch(max_results=3)