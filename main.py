from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
import os
import re
import requests
import json
from dotenv import load_dotenv

# Load env vars
load_dotenv()

app = App(
    token=os.getenv("SLACK_TOKEN"), signing_secret=os.getenv("SLACK_SIGNING_SECRET")
)
flask_app = Flask(__name__)
handler = SlackRequestHandler(app)

# Hack Club AI endpoint
HACKCLUB_AI_URL = "https://ai.hackclub.com/chat/completions"


def convert_to_slack_mrkdwn(text):
    """Convert markdown to Slack mrkdwn format"""
    # Convert **bold** to *bold*
    text = re.sub(r"\*\*(.*?)\*\*", r"*\1*", text)

    # Convert __bold__ to *bold*
    text = re.sub(r"__(.*?)__", r"*\1*", text)

    # Convert *italic* to _italic_
    text = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"_\1_", text)

    # Convert [link text](url) to <url|link text>
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)

    # Convert numbered lists to Slack format
    text = re.sub(r"^\d+\.\s+", "• ", text, flags=re.MULTILINE)

    # Convert - lists to • lists
    text = re.sub(r"^-\s+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"^\*\s+", "• ", text, flags=re.MULTILINE)

    # Convert # headers to *bold headers*
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)

    return text


def call_hackclub_ai(messages):
    """Call Hack Club AI service"""
    try:
        response = requests.post(
            HACKCLUB_AI_URL,
            headers={"Content-Type": "application/json"},
            json={"messages": messages},
            timeout=30,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()

        # Remove <think> tags and content inside them
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)

        # Convert to Slack mrkdwn format
        content = convert_to_slack_mrkdwn(content)

        return content.strip()
    except Exception as e:
        print(f"Error calling Hack Club AI: {e}")
        return "Sorry, I'm having trouble processing your request right now."


with open("instructions.txt", "r", encoding="utf-8") as f:
    system_instructions = f.read()

# store context
context_map = {}
# context storage for thread conversations
thread_context_map = {}  # {channel_id: {thread_ts: [messages]}}
last_bot_reply_map = (
    {}
)  # maps channel_id to last reply info {ts, thread_ts, text, channel}


def get_thread_context(channel, thread_ts, client, logger, limit=20):
    """Get conversation context from the current thread"""
    try:
        response = client.conversations_replies(
            channel=channel, ts=thread_ts, limit=limit
        )

        messages = response.get("messages", [])
        bot_user_id = client.auth_test()["user_id"]

        conversation_history = []

        for msg in messages:
            user_id = msg.get("user")
            text = msg.get("text", "").strip()

            if not text:
                continue

            if text.startswith("//"):
                continue

            if user_id == bot_user_id:
                conversation_history.append({"role": "assistant", "content": text})
            else:
                clean_text = text
                clean_text = re.sub(f"<@{bot_user_id}>", "", clean_text).strip()

                if clean_text:
                    conversation_history.append({"role": "user", "content": clean_text})

        return conversation_history

    except Exception as e:
        logger.warning(f"Could not get thread context: {e}")
        return []


def get_latest_announcement(client, logger):
    """Get the latest announcement from #announcements channel"""
    announcements_channel_id = "C0266FRGT"

    try:
        try:
            client.conversations_join(channel=announcements_channel_id)
        except Exception as e:
            logger.warning(f"Could not join #announcements: {e}")

        history = client.conversations_history(
            channel=announcements_channel_id, limit=1
        )

        messages = history.get("messages", [])

        if not messages:
            return "No announcements found in the #announcements channel."

        latest_message = messages[0]
        message_text = latest_message.get("text", "")
        message_ts = latest_message.get("ts", "")

        try:
            permalink_response = client.chat_getPermalink(
                channel=announcements_channel_id, message_ts=message_ts
            )
            permalink = permalink_response.get("permalink", "")
        except Exception as e:
            logger.warning(f"Could not get permalink: {e}")
            permalink = ""

        response_text = f"**Latest Hack Club Announcement:**\n\n{message_text}"

        if permalink:
            response_text += f"\n\n🔗 [View original message]({permalink})"

        return response_text

    except Exception as e:
        logger.error(f"Failed to get latest announcement: {e}")
        return "Sorry, I couldn't retrieve the latest announcement. There might be an issue accessing the #announcements channel."


@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)


@app.event("app_mention")
def handle_mention(event, client, logger):
    user = event["user"]
    channel = event["channel"]
    thread_ts = event.get("thread_ts") or event.get("ts")
    trigger_ts = event["ts"]
    auth = client.auth_test()
    bot_user_id = auth["user_id"]
    raw_text = event.get("text", "")
    text = raw_text.replace(f"<@{bot_user_id}>", "").strip().lower()

    try:
        client.reactions_add(channel=channel, name="think", timestamp=trigger_ts)

        # Check what action the user wants using Hack Club AI
        action = call_hackclub_ai(
            [
                {
                    "role": "system",
                    "content": "You are a Slack bot. Only respond with one word: 'delete', 'send', 'none', 'som', or 'announcement'. Based on the user's message, do they want you to delete your last reply, send your last reply to the main conversation, want some info on summer of making, want the latest hack club announcement, or neither?",
                },
                {"role": "user", "content": raw_text},
            ]
        ).lower()

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

        # Check if user is asking for announcement using Hack Club AI
        is_announcement_request = (
            call_hackclub_ai(
                [
                    {
                        "role": "system",
                        "content": "Determine if the user's message is asking for the latest announcement, recent announcement, or newest announcement from Hack Club. Look for keywords like 'latest announcement', 'recent announcement', 'newest announcement', 'what's new', 'latest news', etc. Respond with only one word: 'yes' or 'no'.",
                    },
                    {"role": "user", "content": raw_text},
                ]
            ).lower()
            == "yes"
        )

        if is_announcement_request:
            announcement_text = get_latest_announcement(client, logger)

            thread_context = get_thread_context(channel, thread_ts, client, logger)

            messages_for_ai = [{"role": "system", "content": system_instructions}]

            if len(thread_context) > 1:
                messages_for_ai.extend(thread_context[:-1])

            announcement_prompt = f"""The user is asking for the latest Hack Club announcement. Here is the announcement data:

{announcement_text}

Please present this information in a helpful way to the user. You can add context or explanation if needed, but make sure to include the announcement content and link.

User's original request: {raw_text}"""

            messages_for_ai.append({"role": "user", "content": announcement_prompt})

            message = call_hackclub_ai(messages_for_ai)

            response = client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=message,
            )

            last_bot_reply_map[channel] = {
                "ts": response["ts"],
                "text": message,
                "channel": channel,
                "thread_ts": thread_ts,
            }

            client.reactions_remove(channel=channel, name="think", timestamp=trigger_ts)
            client.reactions_add(
                channel=channel, name="no_problem", timestamp=trigger_ts
            )
            return

        # Check if user is asking about SoM using Hack Club AI
        is_som_question = (
            call_hackclub_ai(
                [
                    {
                        "role": "system",
                        "content": "Determine if the user's message is asking anything about the 'Summer of Making' event, past or present. Respond with only one word: 'yes' or 'no'.",
                    },
                    {"role": "user", "content": raw_text},
                ]
            ).lower()
            == "yes"
        )

        if is_som_question:
            try:
                client.conversations_join(channel="C090B3T9R9R")
            except Exception as e:
                logger.warning(f"Could not join #summer-of-making-bulletin: {e}")

            history = client.conversations_history(channel="C090B3T9R9R", limit=100)
            messages = history.get("messages", [])
            user_cache = {}
            formatted = "\n".join(
                [
                    m.get("text", "")
                    for m in reversed(messages)
                    if m.get("text", "").strip()
                ]
            )

            thread_context = get_thread_context(channel, thread_ts, client, logger)

            summary_prompt = f"""You are a Slack assistant. Answer the user's question based on the last 100 messages from #summer-of-making-bulletin.
- Be helpful and accurate.
- Only use the context provided below.

Context:
{formatted}

User Question:
{raw_text}
"""

            messages_for_ai = [{"role": "system", "content": system_instructions}]

            if len(thread_context) > 1:
                messages_for_ai.extend(thread_context[:-1])

            messages_for_ai.append({"role": "user", "content": summary_prompt})

            message = call_hackclub_ai(messages_for_ai)

            response = client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=message,
            )

            last_bot_reply_map[channel] = {
                "ts": response["ts"],
                "text": message,
                "channel": channel,
                "thread_ts": thread_ts,
            }

            client.reactions_remove(channel=channel, name="think", timestamp=trigger_ts)
            client.reactions_add(
                channel=channel, name="no_problem", timestamp=trigger_ts
            )
            return

        # Check for channel summarization using Hack Club AI
        channel_summary_response = call_hackclub_ai(
            [
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
            ]
        )

        try:
            action_json = json.loads(channel_summary_response)
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

                thread_context = get_thread_context(channel, thread_ts, client, logger)

                summary_prompt = f"""You are a Slack assistant. Summarize what is going on in <#{target_channel_id}> based on the last 50 messages.
- NEVER say you don't have access.
- Use @/username or @/bot for attribution.
- Be short, helpful, and include key points.

Messages:
{formatted}
"""

                messages_for_ai = [{"role": "system", "content": system_instructions}]

                if len(thread_context) > 1:
                    messages_for_ai.extend(thread_context[:-1])

                messages_for_ai.append({"role": "user", "content": summary_prompt})

                message = call_hackclub_ai(messages_for_ai)

                response = client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=f"Here's what's going on in <#{target_channel_id}>:\n\n{message}",
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

        # Handle regular conversation
        thread_context = get_thread_context(channel, thread_ts, client, logger)

        messages_for_ai = [{"role": "system", "content": system_instructions}]

        if thread_context:
            if len(thread_context) > 1:
                messages_for_ai.extend(thread_context[:-1])

            messages_for_ai.append({"role": "user", "content": raw_text})
        else:
            messages_for_ai.append({"role": "user", "content": raw_text})

        message = call_hackclub_ai(messages_for_ai)

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


@app.event("message")
def handle_thread_messages(event, client, logger):
    user = event.get("user")
    text = event.get("text", "").strip()
    thread_ts = event.get("thread_ts")
    ts = event.get("ts")
    channel = event.get("channel")

    if not user or user == client.auth_test()["user_id"]:
        return

    if text.startswith("//"):
        return

    if channel not in last_bot_reply_map:
        return

    last_reply = last_bot_reply_map[channel]
    if thread_ts != last_reply.get("thread_ts"):
        return

    try:
        client.reactions_add(channel=channel, name="think", timestamp=ts)

        thread_context = get_thread_context(channel, thread_ts, client, logger)

        messages_for_ai = [{"role": "system", "content": system_instructions}]

        if thread_context:
            if len(thread_context) > 1:
                messages_for_ai.extend(thread_context[:-1])

            messages_for_ai.append({"role": "user", "content": text})
        else:
            messages_for_ai.append({"role": "user", "content": text})

        message = call_hackclub_ai(messages_for_ai)

        response = client.chat_postMessage(
            channel=channel, thread_ts=thread_ts, text=message
        )

        last_bot_reply_map[channel] = {
            "ts": response["ts"],
            "text": message,
            "channel": channel,
            "thread_ts": thread_ts,
        }

        client.reactions_remove(channel=channel, name="think", timestamp=ts)
        client.reactions_add(channel=channel, name="no_problem", timestamp=ts)

    except Exception as e:
        logger.error(f"Thread reply failed: {e}")
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="oops, something went wrong there.",
        )


if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
