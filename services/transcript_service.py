"""
Serwis generowania estetycznych transkryptów HTML dla kanałów zgłoszeń (Ticketów).
"""

import html
import logging
from datetime import datetime
import discord

logger = logging.getLogger("AegisBot.TranscriptService")

class TranscriptService:
    """Generuje czysty i responsywny plik HTML stanowiący archiwum konwersacji w zgłoszeniu."""

    @staticmethod
    async def generate_html(
        channel: discord.TextChannel,
        messages: list[discord.Message]
    ) -> str:
        """
        Pobiera listę wiadomości i generuje pełny dokument HTML z motywem Dark Mode Discorda.
        """
        html_template = """<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Transkrypt Ticket - {channel_name}</title>
    <style>
        body {{
            background-color: #313338;
            color: #dbdee1;
            font-family: 'gg sans', 'Noto Sans', Helvetica, Arial, sans-serif;
            margin: 0;
            padding: 20px;
        }}
        .header {{
            background-color: #2b2d31;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            border-left: 4px solid #5865f2;
        }}
        .header h1 {{
            margin: 0 0 10px 0;
            color: #f2f3f5;
            font-size: 24px;
        }}
        .header p {{
            margin: 5px 0;
            color: #949ba4;
        }}
        .chat {{
            display: flex;
            flex-direction: column;
            gap: 16px;
        }}
        .msg {{
            display: flex;
            gap: 16px;
            background-color: #2b2d31;
            padding: 12px 16px;
            border-radius: 8px;
        }}
        .avatar {{
            width: 40px;
            height: 40px;
            border-radius: 50%;
        }}
        .content {{
            display: flex;
            flex-direction: column;
            flex: 1;
        }}
        .msg-header {{
            display: flex;
            align-items: baseline;
            gap: 8px;
            margin-bottom: 4px;
        }}
        .username {{
            font-weight: 600;
            color: #f2f3f5;
        }}
        .timestamp {{
            font-size: 12px;
            color: #949ba4;
        }}
        .text {{
            font-size: 14px;
            line-height: 1.375;
            white-space: pre-wrap;
            word-break: break-word;
        }}
        .attachment {{
            margin-top: 8px;
            font-size: 13px;
        }}
        .attachment a {{
            color: #00a8fc;
            text-decoration: none;
        }}
        .footer {{
            margin-top: 30px;
            text-align: center;
            font-size: 12px;
            color: #949ba4;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Archiwum Zgłoszenia #{channel_name}</h1>
        <p>Gildia: {guild_name} (ID: {guild_id})</p>
        <p>Wygenerowano: {generated_at}</p>
        <p>Liczba wiadomości: {msg_count}</p>
    </div>
    <div class="chat">
        {messages_html}
    </div>
    <div class="footer">
        Aegis Bot System Transkryptów • Powstało w Pythonie 3.12 z użyciem discord.py 2.x
    </div>
</body>
</html>
"""

        msg_blocks = []
        for msg in messages:
            avatar_url = msg.author.display_avatar.url
            user_name = html.escape(str(msg.author))
            timestamp_str = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            content_text = html.escape(msg.content) if msg.content else "*(Brak treści tekstowej)*"

            attachments_html = ""
            if msg.attachments:
                att_links = [
                    f'<div class="attachment">📎 Załącznik: <a href="{att.url}" target="_blank">{html.escape(att.filename)}</a></div>'
                    for att in msg.attachments
                ]
                attachments_html = "".join(att_links)

            msg_block = f"""
            <div class="msg">
                <img class="avatar" src="{avatar_url}" alt="Avatar">
                <div class="content">
                    <div class="msg-header">
                        <span class="username">{user_name}</span>
                        <span class="timestamp">{timestamp_str}</span>
                    </div>
                    <div class="text">{content_text}</div>
                    {attachments_html}
                </div>
            </div>
            """
            msg_blocks.append(msg_block)

        return html_template.format(
            channel_name=html.escape(channel.name),
            guild_name=html.escape(channel.guild.name),
            guild_id=channel.guild.id,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            msg_count=len(messages),
            messages_html="".join(msg_blocks)
        )
