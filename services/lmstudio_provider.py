import os

from openai import OpenAI

client = OpenAI(
    base_url=os.getenv(
        "LMSTUDIO_BASE_URL",
        "http://localhost:1234/v1"
    ),
    api_key="lm-studio"
)


def ask_lmstudio(prompt):

    response = client.chat.completions.create(
        model="qwen",
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    return response.choices[0].message.content