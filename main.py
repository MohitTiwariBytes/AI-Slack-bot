from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
from openai import OpenAI
import json
import os
from dotenv import load_dotenv

load_dotenv()


app = App(
    token=os.getenv("SLACK_TOKEN"),
    signing_secret=os.getenv("SLACK_SIGNING_SECRET")
)


flask_app = Flask(__name__)
handler = SlackRequestHandler(app)
openai_key = os.getenv("OPENAI_API_KEY")


client = OpenAI(api_key=openai_key)

response = client.responses.create(
    model="gpt-4.1",
    input="Write a one-sentence bedtime story about a unicorn."
)

print(response.output_text)

# @flask_app.route("/slack/events", methods=["POST"])
# def slack_events():
#     return handler.handle(request)

# @app.event("app_mention")
# def handle_mention(event, client, logger):
#     user = event["user"]
#     channel = event["channel"]
#     thread_ts = event.get("thread_ts") or event.get("ts")
#     trigger_ts = event["ts"]
#     bot_user_id = event["blocks"][0]["elements"][0]["elements"][0]["user_id"]
#     text = event.get("text", "").replace(f"<@{bot_user_id}>", "").strip()

#     try:
#         client.reactions_add(channel=channel, name="think", timestamp=trigger_ts)

#         completion = client_ai.chat.completions.create(
#             model="gpt-4.1",
#             messages=[
#                 {"role": "system", "content": system_instructions},
#                 {"role": "user", "content": text}
#             ]
#         )

#         message = completion.choices[0].message.content

#         client.chat_postMessage(
#             channel=channel,
#             thread_ts=thread_ts,
#             text=message
#         )

#         client.reactions_remove(channel=channel, name="think", timestamp=trigger_ts)
#         client.reactions_add(channel=channel, name="no_problem", timestamp=trigger_ts)

#     except Exception as e:
#         logger.error(f"AI reply failed: {e}")
#         client.chat_postMessage(
#             channel=channel,
#             thread_ts=thread_ts,
#             text="yo dude, something broke. My master <@U06MC0G7A4R> might want to check this out."
#         )

if __name__ == "__main__":
    flask_app.run(port=3000)
    # print(os.getenv("OPENAI_API_KEY"))
    # print(openai_key)
    # print(f"SHA256 of .env OpenAI key: {hashed_key}")
