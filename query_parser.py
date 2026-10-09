import json
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2:3b"

def parse_query(query_text):
    prompt = (
        "Extract the people mentioned and the scene description from this search query. "
        "If the entire query is just a person's name with no other description, put it in "
        "\"people\" and leave \"scene\" as an empty string — never a sentence explaining "
        "that no scene was found. "
        "Respond ONLY with JSON in the format {\"people\": [...], \"scene\": \"...\"}.\n"
        "Examples:\n"
        "Query: Jill\n"
        "{\"people\": [\"Jill\"], \"scene\": \"\"}\n"
        "Query: a waterfall\n"
        "{\"people\": [], \"scene\": \"a waterfall\"}\n"
        "Query: Jack and Jill on a hill\n"
        "{\"people\": [\"Jack\", \"Jill\"], \"scene\": \"on a hill\"}\n"
        f"Query: {query_text}"
    )
    print(f"query_text: {query_text!r}")
    
    resp = requests.post(OLLAMA_URL, json={
        "model": MODEL,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0}
    }, timeout=30)
    resp.raise_for_status()
    raw_text = resp.json()["response"]
    print(f"raw_text: {raw_text!r}")

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        print(f"Failed to parse Ollama response as JSON: {raw_text!r}")
        return {"people": [], "scene": query_text}

if __name__ == "__main__":
    import sys
    query = " ".join(sys.argv[1:])
    result = parse_query(query)
    print(result)