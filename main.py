from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
from openai import OpenAI
import os
import re
from dotenv import load_dotenv

# Load env vars
load_dotenv()


app = App(
    token=os.getenv("SLACK_TOKEN"), signing_secret=os.getenv("SLACK_SIGNING_SECRET")
)
flask_app = Flask(__name__)
handler = SlackRequestHandler(app)

client_ai = OpenAI(api_key=os.getenv("OPENAI_API_KEY", "").strip())

with open("instructions.txt", "r", encoding="utf-8") as f:
    system_instructions = f.read()

# store context
context_map = {}
last_bot_reply_map = {}


@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)


@app.event("app_mention")
def handle_mention(event, client, logger):
    user = event["user"]
    channel = event["channel"]
    thread_ts = event.get("thread_ts") or event.get("ts")
    trigger_ts = event["ts"]
    bot_user_id = event["blocks"][0]["elements"][0]["elements"][0]["user_id"]
    raw_text = event.get("text", "")
    text = raw_text.replace(f"<@{bot_user_id}>", "").strip().lower()

    try:
        client.reactions_add(channel=channel, name="think", timestamp=trigger_ts)

        action_check = client_ai.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {
                    "role": "system",
                    "content": "You are a Slack bot. Only respond with one word: 'delete', 'send', or 'none'. Based on the user's message, do they want you to delete your last reply, send your last reply to the main conversation, or neither?",
                },
                {"role": "user", "content": raw_text},
            ],
        )
        action = action_check.choices[0].message.content.strip().lower()

        if action == "delete":
            last = last_bot_reply_map.get(channel)
            if last:
                client.chat_delete(channel=last["channel"], ts=last["ts"])
                last_bot_reply_map.pop(channel, None)
                client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text="Got it. Deleted my last reply.",
                )
            else:
                client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text="No last message to delete.",
                )
            client.reactions_remove(channel=channel, name="think", timestamp=trigger_ts)
            client.reactions_add(
                channel=channel, name="no_problem", timestamp=trigger_ts
            )
            return

        if action == "send":
            last = last_bot_reply_map.get(channel)
            if last:
                client.chat_postMessage(channel=channel, text=last["text"])
            else:
                client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text="No last message to send.",
                )
            client.reactions_remove(channel=channel, name="think", timestamp=trigger_ts)
            client.reactions_add(
                channel=channel, name="no_problem", timestamp=trigger_ts
            )
            return

        channel_summary_check = client_ai.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {
                    "role": "system",
                    "content": """
You are a Slack bot. Determine if the user's message is asking you to summarize messages from a Slack channel.

Rules:
- ONLY respond in JSON like this:
  { "action": "summarize", "channel_id": "CXXXXXXX" }
  or
  { "action": "none" }

- If the user is asking to summarize what's going on in a channel they mentioned, respond with 'summarize' and include the Slack channel ID (e.g., C12345678).
- Slack formats mentions like: "<#C04ABC123|channel-name>"
""",
                },
                {"role": "user", "content": raw_text},
            ],
        )

        try:
            action_json = eval(channel_summary_check.choices[0].message.content.strip())
            if action_json.get("action") == "summarize" and action_json.get(
                "channel_id"
            ):
                target_channel_id = action_json["channel_id"]

                try:
                    client.conversations_join(channel=target_channel_id)
                except Exception as e:
                    logger.warning(f"Could not join channel {target_channel_id}: {e}")

                history = client.conversations_history(
                    channel=target_channel_id, limit=50
                )
                messages = history.get("messages", [])
                user_cache = {}

                def get_username(user_id):
                    if user_id in user_cache:
                        return user_cache[user_id]
                    try:
                        info = client.users_info(user=user_id)
                        username = info["user"]["name"]
                        user_cache[user_id] = username
                        return username
                    except Exception:
                        return f"U/{user_id}"

                formatted = "\n".join(
                    [
                        f"@/{get_username(m['user']) if 'user' in m else 'bot'}: {m.get('text', '')}"
                        for m in reversed(messages)
                        if m.get("text", "").strip()
                    ]
                )

                summary_prompt = f"""You are a Slack assistant. Summarize what is going on in <#{target_channel_id}> based on the last 50 messages.

- NEVER say you don’t have access.
- Use @/username or @/bot for attribution.
- Be short, helpful, and include key points.

Messages:
{formatted}
"""

                completion = client_ai.chat.completions.create(
                    model="gpt-4.1",
                    messages=[
                        {"role": "system", "content": system_instructions},
                        {"role": "user", "content": summary_prompt},
                    ],
                )

                message = completion.choices[0].message.content.strip()

                response = client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=f"Here’s what’s going on in <#{target_channel_id}>:\n\n{message}",
                )

                last_bot_reply_map[channel] = {
                    "ts": response["ts"],
                    "text": message,
                    "channel": channel,
                    "thread_ts": thread_ts,
                }

                client.reactions_remove(
                    channel=channel, name="think", timestamp=trigger_ts
                )
                client.reactions_add(
                    channel=channel, name="no_problem", timestamp=trigger_ts
                )
                return
        except Exception as e:
            logger.warning(f"Channel summarization detection failed: {e}")

        completion = client_ai.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": raw_text},
            ],
        )

        message = completion.choices[0].message.content.strip()

        response = client.chat_postMessage(
            channel=channel, thread_ts=thread_ts, text=message
        )

        last_bot_reply_map[channel] = {
            "ts": response["ts"],
            "text": message,
            "channel": channel,
            "thread_ts": thread_ts,
        }

        client.reactions_remove(channel=channel, name="think", timestamp=trigger_ts)
        client.reactions_add(channel=channel, name="no_problem", timestamp=trigger_ts)

    except Exception as e:
        logger.error(f"AI reply failed: {e}")
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="yo dude, something broke. My master <@U06MC0G7A4R> might want to check this out.",
        )


if __name__ == "__main__":
    flask_app.run(port=3000)
