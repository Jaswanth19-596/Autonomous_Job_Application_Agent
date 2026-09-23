import requests
import json

from dotenv import load_dotenv
load_dotenv()
import os

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

class JevTokenLimitExceededException(Exception):
    def __init__(self, error_dict: dict):
        self.message = error_dict['message']
        self.code = error_dict['code']
        self.error_dict = error_dict
        super().__init__(f"{self.code} : {self.message}")



def use_jev(state: str, questions: dict):
    response = requests.post(
        url="https://openrouter.ai/api/alpha/decisions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "",
            "X-OpenRouter-Title": "",
        },
        timeout=30,
        data=json.dumps({
            "model": "~typesafe/jev-latest",
            "state": state,
            "questions": questions
        })
    )
    payload = response.json()
    
    if "error" in payload:
        raise JevTokenLimitExceededException(payload["error"])

    answers = payload["answers"]
    return answers



