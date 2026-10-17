"""
Moduł Web Dashboard dla bota AegisBot / Team77 (WebDashboardCog).

Uruchamia wbudowany, asynchroniczny serwer aiohttp.web w ramach pętli zdarzeń bota,
udostępniający:
1. Serwowanie statycznych plików panelu z katalogu website/ (HTML/CSS/JS/obrazy).
2. Publiczne API stanu bota, serwera MTA, audycji Radia Project FM i rankingów.
3. Bezpieczne API zarządzania dla zarządu (dostęp chroniony hasłem sesyjnym):
   - przegląd i zatwierdzanie wpłat do skarbca,
   - wysyłanie ogłoszeń i embedów na dowolny kanał Discorda,
   - statystyki aktywności członków.
"""

import asyncio
import logging
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any, Dict, Set

import aiohttp
from aiohttp import web
import discord
from discord.ext import commands

import config
from utils.embeds import create_embed

logger = logging.getLogger("AegisBot.Cogs.WebDashboard")

WEBSITE_DIR = config.BASE_DIR / "website"


class WebDashboardCog(commands.Cog, name="WebDashboardCog"):
    """Zarządza wbudowanym serwerem HTTP dla panelu zarządzania bota."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._start_time = time.time()
        self._admin_sessions: Dict[str, float] = {}  # token -> timestamp utworzenia
        self._session_ttl_seconds = 7 * 24 * 3600    # 7 dni ważności sesji

    async def cog_load(self) -> None:
        if not getattr(config, "DASHBOARD_ENABLED", True):
            logger.info("Panel webowy (Web Dashboard) jest wyłączony w konfiguracji.")
            return

        try:
            await self._start_web_server()
        except Exception as e:
            logger.error(f"Nie udało się uruchomić serwera Web Dashboard: {e}", exc_info=True)

    async def cog_unload(self) -> None:
        await self._stop_web_server()

    @web.middleware
    async def _cors_middleware(self, request: web.Request, handler):
        if request.method == "OPTIONS":
            resp = web.Response(status=204)
        else:
            try:
                resp = await handler(request)
            except web.HTTPException as ex:
                resp = ex
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Dashboard-Token"
        return resp

    async def _start_web_server(self) -> None:
        """Konfiguruje i uruchamia aplikację aiohttp.web."""
        app = web.Application(middlewares=[self._cors_middleware])

        # Routing API
        app.router.add_get("/api/status", self._handle_api_status)
        app.router.add_get("/api/leaderboards", self._handle_api_leaderboards)
        app.router.add_post("/api/login", self._handle_api_login)
        app.router.add_get("/api/donations", self._handle_api_donations)
        app.router.add_post("/api/donations/{id}/decision", self._handle_api_donation_decision)
        app.router.add_get("/api/channels", self._handle_api_channels)
        app.router.add_post("/api/announcement", self._handle_api_announcement)
        app.router.add_get("/api/guild/overview", self._handle_api_guild_overview)
        app.router.add_get("/api/guild/members", self._handle_api_guild_members)
        app.router.add_get("/api/applications", self._handle_api_applications)
        app.router.add_post("/api/applications/{id}/decision", self._handle_api_application_decision)
        app.router.add_delete("/api/applications/{id}", self._handle_api_application_delete)
        app.router.add_post("/api/applications/bulk-delete", self._handle_api_application_bulk_delete)
        app.router.add_get("/api/fee-check", self._handle_api_fee_check)
        app.router.add_post("/api/fee-check/mark-paid", self._handle_api_fee_mark_paid)
        app.router.add_post("/api/fee-check/remind", self._handle_api_fee_remind)
        app.router.add_get("/api/strikes", self._handle_api_strikes)
        app.router.add_post("/api/strikes/add", self._handle_api_strike_add)
        app.router.add_post("/api/strikes/{id}/delete", self._handle_api_strike_delete)
        app.router.add_get("/api/projectrpg/lookup", self._handle_api_projectrpg_lookup)

        # Serwowanie strony głównej i statyków
        app.router.add_get("/", self._handle_serve_index)
        if WEBSITE_DIR.exists():
            app.router.add_static("/static", WEBSITE_DIR, show_index=False)
            app.router.add_get("/{filename:[^/]+\\.(png|jpg|jpeg|gif|ico|svg|css|js)}", self._handle_serve_static_root)

        self._app = app
        self._runner = web.AppRunner(app)
        await self._runner.setup()

        host = getattr(config, "DASHBOARD_HOST", "0.0.0.0")
        port = getattr(config, "DASHBOARD_PORT", 8080)
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()

        logger.info(f"🚀 Web Dashboard uruchomiony na porcie {port} (nasłuchiwanie: {host}:{port})")
        logger.info(f"👉 Dostęp lokalny: http://localhost:{port} | Dostęp z zewnątrz (GameHost/VPS): http://<ADRES_IP_SERWERA>:{port}")

    async def _stop_web_server(self) -> None:
        """Bezpiecznie zamyka serwer HTTP."""
        if self._site:
            await self._site.stop()
            self._site = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        logger.info("Web Dashboard został zatrzymany.")

    # ─── POMOCNICZE / AUTORYZACJA ───

    def _is_authenticated(self, request: web.Request) -> bool:
        """Sprawdza ważność tokenu sesji w nagłówku Authorization lub ciasteczku."""
        auth_header = request.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        elif "X-Dashboard-Token" in request.headers:
            token = request.headers["X-Dashboard-Token"].strip()
        elif "dashboard_token" in request.cookies:
            token = request.cookies["dashboard_token"]

        if not token or token not in self._admin_sessions:
            return False

        created_at = self._admin_sessions[token]
        if time.time() - created_at > self._session_ttl_seconds:
            self._admin_sessions.pop(token, None)
            return False

        return True

    def _get_primary_guild(self) -> Optional[discord.Guild]:
        """Zwraca główny serwer bota."""
        target_id = getattr(config, "GUILD_ID", 0) or getattr(config, "TEST_GUILD_ID", 0)
        if target_id:
            g = self.bot.get_guild(target_id)
            if g:
                return g
            logger.warning(f"Bot nie znajduje się na serwerze Discord o ID {target_id} (upewnij się, że bot został zaproszony na ten serwer).")
        if self.bot.guilds:
            return self.bot.guilds[0]
        return None

    # ─── HANDLERY PLIKÓW STATYCZNYCH ───

    async def _handle_serve_index(self, request: web.Request) -> web.Response:
        index_file = WEBSITE_DIR / "index.html"
        if not index_file.exists():
            return web.Response(text="Brak pliku website/index.html", status=404)
        return web.FileResponse(index_file)

    async def _handle_serve_static_root(self, request: web.Request) -> web.Response:
        filename = request.match_info.get("filename", "")
        filepath = WEBSITE_DIR / filename
        if filepath.exists() and filepath.is_file():
            return web.FileResponse(filepath)
        return web.Response(status=404)

    # ─── ENDPOINTY API ───

    async def _handle_api_login(self, request: web.Request) -> web.Response:
        """Logowanie do panelu administracyjnego."""
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"success": False, "error": "Nieprawidłowe dane JSON"}, status=400)

        password = data.get("password", "").strip()
        expected = getattr(config, "DASHBOARD_PASSWORD", "team77admin")

        if secrets.compare_digest(password, expected):
            token = secrets.token_hex(24)
            self._admin_sessions[token] = time.time()
            resp = web.json_response({"success": True, "token": token, "message": "Zalogowano pomyślnie."})
            resp.set_cookie("dashboard_token", token, max_age=int(self._session_ttl_seconds), httponly=False)
            return resp
        else:
            return web.json_response({"success": False, "error": "Nieprawidłowe hasło administracyjne."}, status=401)

    async def _handle_api_status(self, request: web.Request) -> web.Response:
        """Zwraca ogólne metryki bota, społeczności, skarbca i statusu serwera MTA."""
        uptime_sec = int(time.time() - self._start_time)
        guild = self._get_primary_guild()

        total_members = guild.member_count if guild else sum(g.member_count or 0 for g in self.bot.guilds)
        voice_members_count = 0
        online_members_count = 0

        if guild:
            for m in guild.members:
                if m.voice and m.voice.channel:
                    voice_members_count += 1
                if m.status and m.status != discord.Status.offline:
                    online_members_count += 1

        # Pobieranie sumy skarbca i oczekujących wpłat
        treasury_total = 0
        pending_donations = 0
        try:
            row = await self.bot.db.fetchone(
                "SELECT COALESCE(SUM(amount), 0) as total FROM donations WHERE status = 'approved' AND donation_type = 'dobrowolne'"
            )
            if row:
                treasury_total = int(row["total"] or 0)
            p_row = await self.bot.db.fetchone(
                "SELECT COUNT(*) as cnt FROM donations WHERE status = 'pending'"
            )
            if p_row:
                pending_donations = int(p_row["cnt"] or 0)
        except Exception as e:
            logger.debug(f"Błąd odczytu skarbca dla API: {e}")

        # Liczba oczekujących podań rekrutacyjnych
        pending_applications = 0
        try:
            app_row = await self.bot.db.fetchone("SELECT COUNT(*) as cnt FROM applications WHERE status = 'pending'")
            if app_row:
                pending_applications = int(app_row["cnt"] or 0)
        except Exception as e:
            logger.debug(f"Błąd odczytu podań dla API: {e}")

        # Liczba osób bez wpisowego
        unpaid_fees_count = 0
        if guild:
            team77_role = discord.utils.find(lambda r: r.name.lower() == "team77", guild.roles)
            wpisowe_role = discord.utils.find(lambda r: r.name.lower() == "wpisowe", guild.roles)
            if team77_role:
                for m in guild.members:
                    if not getattr(m, "bot", False) and team77_role in m.roles:
                        if not (wpisowe_role and wpisowe_role in m.roles):
                            unpaid_fees_count += 1

        # Ramówka Radia Project FM oraz status streamu bota na Discordzie
        radio_current = None
        radio_next = None
        bot_streaming_discord = False
        bot_voice_channel_name = None
        stream_title = ""

        music_cog = self.bot.get_cog("AudycjaCog") or self.bot.get_cog("MusicCog")
        if music_cog and hasattr(music_cog, "_get_schedule_shows"):
            _, cur, nxt, _ = music_cog._get_schedule_shows()
            if cur:
                radio_current = {
                    "name": cur.get("name"),
                    "host": (cur.get("user") or {}).get("username"),
                    "dateStart": cur.get("dateStart"),
                    "dateEnd": cur.get("dateEnd"),
                }
            if nxt:
                radio_next = {
                    "name": nxt.get("name"),
                    "host": (nxt.get("user") or {}).get("username"),
                    "dateStart": nxt.get("dateStart"),
                    "dateEnd": nxt.get("dateEnd"),
                }

        # Sprawdzenie czy bot odtwarza strumień w kanale głosowym
        if guild and guild.voice_client and guild.voice_client.is_connected():
            vc = guild.voice_client
            bot_streaming_discord = bool(getattr(vc, "is_playing", lambda: False)())
            if vc.channel:
                bot_voice_channel_name = vc.channel.name

        # Status serwera MTA ProjectRPG
        mta_status = {"online": True, "players": 0, "max_players": 500, "ping": "n/a"}
        prpg_cog = self.bot.get_cog("ProjectRPGCog")
        if prpg_cog and hasattr(prpg_cog, "_cached_server_status") and prpg_cog._cached_server_status:
            cs = prpg_cog._cached_server_status
            mta_status = {
                "online": cs.get("online", True),
                "players": cs.get("players", 0),
                "max_players": cs.get("max_players", 500),
                "ping": cs.get("ping", "n/a"),
            }

        return web.json_response({
            "bot": {
                "name": config.BOT_NAME,
                "latency_ms": round(self.bot.latency * 1000, 1) if self.bot.latency else 0,
                "uptime_seconds": uptime_sec,
                "guild_count": len(self.bot.guilds),
                "is_ready": self.bot.is_ready(),
            },
            "community": {
                "guild_name": guild.name if guild else "Team77",
                "total_members": total_members,
                "online_members": online_members_count,
                "voice_active": voice_members_count,
            },
            "treasury": {
                "total_collected": treasury_total,
                "pending_count": pending_donations,
                "unpaid_fees_count": unpaid_fees_count,
            },
            "recruitment": {
                "pending_count": pending_applications,
            },
            "radio": {
                "current": radio_current,
                "next": radio_next,
                "stream_url": getattr(config, "RADIO_PROJECTFM_URL", "https://panelradiowy.projectrpg.pl/listen/projectfm/main.mp3"),
                "website_url": getattr(config, "RADIO_PROJECTFM_WEBSITE", "https://radio.projectrpg.pl"),
                "bot_streaming": bot_streaming_discord,
                "bot_channel": bot_voice_channel_name,
            },
            "mta": mta_status,
        })

    async def _handle_api_leaderboards(self, request: web.Request) -> web.Response:
        """Zwraca rankingi honorowych dawców skarbca, poziomów XP i czasu głosowego."""
        guild = self._get_primary_guild()
        guild_id = guild.id if guild else (config.TEST_GUILD_ID or 0)

        # 1. Top Donatorzy (skarbnik)
        top_donors = []
        try:
            rows = await self.bot.db.fetchall(
                """
                SELECT user_id, nick, login, SUM(amount) as total_amount, COUNT(*) as donation_count
                FROM donations
                WHERE status = 'approved' AND donation_type = 'dobrowolne'
                GROUP BY user_id
                ORDER BY total_amount DESC
                LIMIT 10
                """
            )
            for r in rows:
                member_name = r["nick"] or r["login"] or f"UID: {r['user_id']}"
                if guild:
                    m = guild.get_member(r["user_id"])
                    if m:
                        member_name = m.display_name
                top_donors.append({
                    "user_id": r["user_id"],
                    "name": member_name,
                    "amount": int(r["total_amount"]),
                    "count": int(r["donation_count"]),
                })
        except Exception as e:
            logger.debug(f"Błąd odczytu top donatorów: {e}")

        # 2. Top XP / Poziomy
        top_levels = []
        try:
            rows = await self.bot.db.fetchall(
                """
                SELECT user_id, xp, level
                FROM levels
                WHERE guild_id = ?
                ORDER BY level DESC, xp DESC
                LIMIT 10
                """,
                (guild_id,)
            )
            for r in rows:
                member_name = f"Użytkownik {r['user_id']}"
                avatar_url = ""
                if guild:
                    m = guild.get_member(r["user_id"])
                    if m:
                        member_name = m.display_name
                        avatar_url = m.display_avatar.url
                top_levels.append({
                    "user_id": r["user_id"],
                    "name": member_name,
                    "level": r["level"],
                    "xp": r["xp"],
                    "avatar": avatar_url,
                })
        except Exception as e:
            logger.debug(f"Błąd odczytu top levels: {e}")

        # 3. Top Czas na kanałach głosowych (all-time)
        top_voice = []
        try:
            rows = await self.bot.db.fetchall(
                """
                SELECT user_id, SUM(seconds) as total_seconds
                FROM voice_activity
                WHERE guild_id = ?
                GROUP BY user_id
                ORDER BY total_seconds DESC
                LIMIT 10
                """,
                (guild_id,)
            )
            for r in rows:
                member_name = f"Użytkownik {r['user_id']}"
                if guild:
                    m = guild.get_member(r["user_id"])
                    if m:
                        member_name = m.display_name
                sec = int(r["total_seconds"] or 0)
                hours = sec // 3600
                mins = (sec % 3600) // 60
                top_voice.append({
                    "user_id": r["user_id"],
                    "name": member_name,
                    "seconds": sec,
                    "formatted": f"{hours}h {mins}m" if hours > 0 else f"{mins}m",
                })
        except Exception as e:
            logger.debug(f"Błąd odczytu top voice: {e}")

        return web.json_response({
            "top_donors": top_donors,
            "top_levels": top_levels,
            "top_voice": top_voice,
        })

    async def _handle_api_donations(self, request: web.Request) -> web.Response:
        """Zwraca listę wpłat do weryfikacji przez zarząd (wymaga autoryzacji)."""
        if not self._is_authenticated(request):
            return web.json_response({"error": "Wymagana autoryzacja zarządu."}, status=401)

        try:
            rows = await self.bot.db.fetchall(
                """
                SELECT donation_id, user_id, amount, donation_type, status, source,
                       proof_url, reason, submitted_by, nick, login, created_at
                FROM donations
                ORDER BY CASE WHEN status = 'pending' THEN 0 ELSE 1 END, donation_id DESC
                LIMIT 50
                """
            )
            guild = self._get_primary_guild()
            items = []
            for r in rows:
                name = r["nick"] or r["login"] or f"ID: {r['user_id']}"
                if guild:
                    m = guild.get_member(r["user_id"])
                    if m:
                        name = m.display_name
                items.append({
                    "id": r["donation_id"],
                    "user_id": r["user_id"],
                    "name": name,
                    "amount": r["amount"],
                    "type": r["donation_type"],
                    "status": r["status"],
                    "proof_url": r["proof_url"],
                    "reason": r["reason"],
                    "created_at": str(r["created_at"]),
                })
            return web.json_response({"donations": items})
        except Exception as e:
            logger.error(f"Błąd pobierania wpłat: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_api_donation_decision(self, request: web.Request) -> web.Response:
        """Zatwierdza lub odrzuca wpłatę przez panel WWW (wymaga autoryzacji zarządu).
        Automatycznie synchronizuje status na Discordzie, publikuje ogłoszenie wpłaty i powiadamia gracza."""
        if not self._is_authenticated(request):
            return web.json_response({"error": "Wymagana autoryzacja zarządu."}, status=401)

        donation_id = request.match_info.get("id")
        if not donation_id or not donation_id.isdigit():
            return web.json_response({"error": "Nieprawidłowe ID wpłaty."}, status=400)

        try:
            body = await request.json()
            action = body.get("action", "").lower().strip()
            if action not in ("approved", "rejected"):
                return web.json_response({"error": "Dozwolone akcje to 'approved' lub 'rejected'."}, status=400)

            d_id = int(donation_id)
            guild = self._get_primary_guild()
            is_approve = (action == "approved")

            row = await self.bot.db.fetchone("SELECT * FROM donations WHERE donation_id = ?", (d_id,))
            if not row:
                return web.json_response({"error": "Nie znaleziono wpłaty o podanym ID."}, status=404)

            # 1. Przetworzenie przez serwis wpłat lub fallback do bazy
            donation_service = getattr(self.bot, "donation_service", None)
            if donation_service:
                success, msg, donation = await donation_service.decide(d_id, 0, is_approve)
                if not success and row["status"] == "pending":
                    return web.json_response({"error": msg}, status=400)
            else:
                await self.bot.db.execute(
                    "UPDATE donations SET status = ?, decided_by = 0, decided_at = CURRENT_TIMESTAMP WHERE donation_id = ?",
                    (action, d_id)
                )

            # Pobierz zaktualizowany rekord
            updated_row = await self.bot.db.fetchone("SELECT * FROM donations WHERE donation_id = ?", (d_id,))
            donation = dict(updated_row) if updated_row else dict(row)

            # 2. Jeśli zaakceptowano — wyślij oficjalne ogłoszenie wpłaty na Discordzie
            if is_approve and guild:
                try:
                    from views.donation_views import build_announcement_embed
                    announcement = build_announcement_embed(
                        user_id=donation["user_id"],
                        amount=donation["amount"],
                        donation_type=donation["donation_type"],
                        nick=donation.get("nick") or "-",
                        login=donation.get("login") or "-",
                        proof_url=donation.get("proof_url"),
                        reviewer=None,
                        guild_name=guild.name,
                    )

                    announce_row = await self.bot.db.fetchone(
                        "SELECT donation_announce_channel_id FROM guild_config WHERE guild_id = ?",
                        (guild.id,)
                    )
                    announce_ch = None
                    if announce_row and announce_row["donation_announce_channel_id"]:
                        announce_ch = guild.get_channel(announce_row["donation_announce_channel_id"])

                    if announce_ch and isinstance(announce_ch, discord.TextChannel):
                        await announce_ch.send(embed=announcement)
                        # Odświeżenie przyklejonej instrukcji / rankingu wpłat na dole kanału
                        donations_cog = self.bot.get_cog("DonationsCog")
                        if donations_cog and hasattr(donations_cog, "refresh_donation_reminder"):
                            await donations_cog.refresh_donation_reminder(guild, announce_ch)
                except Exception as ex_ann:
                    logger.warning(f"Błąd wysyłania publicznego ogłoszenia wpłaty #{d_id}: {ex_ann}")

            # 3. Zaktualizuj wiadomość weryfikacyjną na Discordzie, jeśli istnieje
            review_msg_id = row.get("review_message_id")
            if review_msg_id and guild:
                try:
                    from views.donation_views import build_review_embed
                    review_ch = None
                    r_row = await self.bot.db.fetchone(
                        "SELECT donation_review_channel_id FROM guild_config WHERE guild_id = ?",
                        (guild.id,)
                    )
                    if r_row and r_row["donation_review_channel_id"]:
                        review_ch = guild.get_channel(r_row["donation_review_channel_id"])

                    if review_ch and isinstance(review_ch, discord.TextChannel):
                        try:
                            msg = await review_ch.fetch_message(review_msg_id)
                            new_embed = build_review_embed(
                                donation_id=d_id,
                                user_id=donation["user_id"],
                                amount=donation["amount"],
                                donation_type=donation["donation_type"],
                                nick=donation.get("nick") or "-",
                                login=donation.get("login") or "-",
                                proof_url=donation.get("proof_url"),
                                status=action,
                                reviewer=None,
                            )
                            await msg.edit(embed=new_embed, view=None)
                        except discord.NotFound:
                            pass
                except Exception as ex_rev:
                    logger.debug(f"Błąd aktualizacji wiadomości na Discordzie: {ex_rev}")

            # 4. DM z potwierdzeniem do gracza
            if guild:
                try:
                    member = guild.get_member(donation["user_id"])
                    if member:
                        if is_approve:
                            await member.send(
                                embed=create_embed(
                                    title="🎉 Wpłata Zaakceptowana!",
                                    description=(
                                        f"Twoja wpłata w kwocie **{donation['amount']:,} PLN** ({donation['donation_type']}) "
                                        f"została pomyślnie zatwierdzona przez Zarząd w panelu organizacji!"
                                    ),
                                    color=config.SUCCESS_COLOR
                                )
                            )
                        else:
                            await member.send(
                                embed=create_embed(
                                    title="❌ Wpłata Odrzucona",
                                    description=(
                                        f"Twoja wpłata w kwocie **{donation['amount']:,} PLN** ({donation['donation_type']}) "
                                        f"została odrzucona przez Zarząd w panelu organizacji."
                                    ),
                                    color=config.DANGER_COLOR
                                )
                            )
                except Exception:
                    pass

            logger.info(f"Wpłata #{donation_id} została oznaczona jako '{action}' przez panel webowy i zsynchronizowana z Discordem.")
            return web.json_response({"success": True, "status": action})
        except Exception as e:
            logger.error(f"Błąd aktualizacji decyzji wpłaty #{donation_id}: {e}", exc_info=True)
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_api_channels(self, request: web.Request) -> web.Response:
        """Zwraca listę kanałów tekstowych Discorda (wymaga autoryzacji zarządu)."""
        if not self._is_authenticated(request):
            return web.json_response({"error": "Wymagana autoryzacja zarządu."}, status=401)

        guild = self._get_primary_guild()
        if not guild:
            return web.json_response({"channels": []})

        channels = []
        for ch in guild.text_channels:
            perms = ch.permissions_for(guild.me)
            if perms.send_messages and perms.embed_links:
                channels.append({
                    "id": str(ch.id),
                    "name": f"#{ch.name}",
                    "category": ch.category.name if ch.category else "Inne",
                })

        channels.sort(key=lambda x: (x["category"], x["name"]))
        return web.json_response({"channels": channels})

    async def _handle_api_announcement(self, request: web.Request) -> web.Response:
        """Wysyła ogłoszenie / embed na wybrany kanał Discorda (wymaga autoryzacji zarządu)."""
        if not self._is_authenticated(request):
            return web.json_response({"error": "Wymagana autoryzacja zarządu."}, status=401)

        try:
            body = await request.json()
            channel_id_str = body.get("channel_id")
            title = body.get("title", "").strip()
            content = body.get("content", "").strip()
            color_hex = str(body.get("color", "#5865F2")).strip().lstrip("#")
            image_url = body.get("image_url", "").strip() or None
            thumbnail_url = body.get("thumbnail_url", "").strip() or None
            title_url = body.get("title_url", "").strip() or None
            plain_text = body.get("plain_text", "").strip()
            include_timestamp = body.get("timestamp", True)

            # Mentions: ping_type can be 'everyone', 'here', 'none' or fallback to ping_everyone boolean
            ping_type = body.get("ping_type")
            if not ping_type:
                ping_type = "everyone" if body.get("ping_everyone") else "none"

            # Author data
            author_data = body.get("author") or {}
            author_name = (author_data.get("name") if isinstance(author_data, dict) else str(author_data)) or ""
            author_name = author_name.strip()[:256] or None
            author_icon_url = (author_data.get("icon_url") if isinstance(author_data, dict) else "") or None
            author_url = (author_data.get("url") if isinstance(author_data, dict) else "") or None

            # Footer data
            footer_data = body.get("footer") or {}
            footer_text = (footer_data.get("text") if isinstance(footer_data, dict) else str(footer_data)) or ""
            footer_text = footer_text.strip()[:2048] if footer_text else None
            footer_icon_url = (footer_data.get("icon_url") if isinstance(footer_data, dict) else "") or None

            # Fields data
            raw_fields = body.get("fields", [])
            valid_fields = []
            if isinstance(raw_fields, list):
                for f in raw_fields[:25]:
                    f_name = str(f.get("name", "")).strip()[:256]
                    f_val = str(f.get("value", "")).strip()[:1024]
                    if f_name or f_val:
                        valid_fields.append({
                            "name": f_name or "\u200b",
                            "value": f_val or "\u200b",
                            "inline": bool(f.get("inline", False))
                        })

            if not channel_id_str or (not content and not valid_fields and not title):
                return web.json_response({"error": "Wymagane jest wskazanie kanału oraz podanie treści lub pól ogłoszenia."}, status=400)

            channel = self.bot.get_channel(int(channel_id_str))
            if not channel or not isinstance(channel, discord.TextChannel):
                return web.json_response({"error": "Nie znaleziono wskazanego kanału tekstowego na serwerze."}, status=404)

            try:
                embed_color = int(color_hex, 16)
            except ValueError:
                embed_color = config.DEFAULT_EMBED_COLOR

            embed = create_embed(
                title=title[:256] if title else "📢 Ogłoszenie Organizacji Team77",
                description=content[:4096] if content else None,
                color=embed_color,
                fields=valid_fields if valid_fields else None,
                footer_text=footer_text or config.FOOTER_TEXT,
                footer_icon_url=footer_icon_url or config.FOOTER_ICON_URL or None,
                thumbnail_url=thumbnail_url,
                image_url=image_url,
                url=title_url,
                timestamp=bool(include_timestamp),
                author_name=author_name,
                author_icon_url=author_icon_url,
                author_url=author_url
            )

            # Build message mention / outer content
            mention_prefix = ""
            if ping_type == "everyone":
                mention_prefix = "@everyone "
            elif ping_type == "here":
                mention_prefix = "@here "

            final_text = (mention_prefix + plain_text).strip() or None

            msg = await channel.send(content=final_text, embed=embed)
            logger.info(f"Wysłano zaawansowane ogłoszenie z panelu WWW na kanał #{channel.name} (msg: {msg.id}).")
            return web.json_response({"success": True, "message_id": str(msg.id), "jump_url": msg.jump_url})
        except Exception as e:
            logger.error(f"Błąd wysyłania ogłoszenia: {e}", exc_info=True)
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_api_guild_overview(self, request: web.Request) -> web.Response:
        """Zwraca wykaz prezenterów i statystyk gildii."""
        guild = self._get_primary_guild()
        if not guild:
            return web.json_response({"presenters": [], "roles_count": 0, "channels_count": 0})

        presenters = []
        for m in guild.members:
            if any(r.name.lower() == "prezenter" for r in m.roles):
                presenters.append({
                    "id": str(m.id),
                    "name": m.display_name,
                    "avatar": m.display_avatar.url,
                    "status": str(m.status),
                })

        return web.json_response({
            "presenters": presenters,
            "roles_count": len(guild.roles),
            "channels_count": len(guild.channels),
        })

    async def _handle_api_guild_members(self, request: web.Request) -> web.Response:
        """Zwraca listę członków serwera Discord (np. do formularzy wyboru)."""
        guild = self._get_primary_guild()
        if not guild:
            return web.json_response({"members": []})
        members = []
        for m in guild.members:
            if not m.bot:
                members.append({
                    "id": str(m.id),
                    "name": m.display_name,
                    "username": str(m),
                    "avatar": m.display_avatar.url,
                })
        members.sort(key=lambda x: x["name"].lower())
        return web.json_response({"members": members})

    # ─── MODUŁ 1: REKRUTACJA & PODANIA ───

    async def _handle_api_applications(self, request: web.Request) -> web.Response:
        """Zwraca listę nadesłanych podań do organizacji."""
        if not self._is_authenticated(request):
            return web.json_response({"error": "Wymagana autoryzacja zarządu."}, status=401)
        guild = self._get_primary_guild()
        guild_id = guild.id if guild else (getattr(config, "GUILD_ID", 0) or 0)

        rows = await self.bot.db.fetchall(
            """
            SELECT id, user_id, nick, login, uid, age, rp_points, recommended_by,
                   rules_agreed, fee_agreed, forum_url, status, decision_by_name,
                   decision_reason, created_at, updated_at
            FROM applications
            WHERE guild_id = ?
            ORDER BY id DESC
            LIMIT 100
            """,
            (guild_id,)
        )
        apps = []
        for r in rows:
            m_name = r["nick"] or r["login"] or f"ID: {r['user_id']}"
            avatar_url = ""
            if guild:
                m = guild.get_member(r["user_id"])
                if m:
                    m_name = m.display_name
                    avatar_url = m.display_avatar.url
            apps.append({
                "id": r["id"],
                "user_id": str(r["user_id"]),
                "name": m_name,
                "discord_name": m_name,
                "avatar": avatar_url,
                "nick": r["nick"] or "",
                "login": r["login"] or "",
                "uid": r["uid"] or "",
                "pid": r["uid"] or "",
                "age": r["age"] or "",
                "why": r["why"] if "why" in r.keys() and r["why"] else "",
                "rp_points": r["rp_points"] or "",
                "hours": r["rp_points"] or "",
                "recommended_by": r["recommended_by"] or "",
                "rules_agreed": r["rules_agreed"] or "",
                "fee_agreed": r["fee_agreed"] or "",
                "forum_url": r["forum_url"] or "",
                "status": (r["status"] or "pending").lower(),
                "decision_by_name": r["decision_by_name"] or "",
                "decision_reason": r["decision_reason"] or "",
                "created_at": str(r["created_at"]) if r["created_at"] else "",
            })
        return web.json_response({"applications": apps})

    async def _handle_api_application_decision(self, request: web.Request) -> web.Response:
        """Podejmuje decyzję o akceptacji lub odrzuceniu podania."""
        if not self._is_authenticated(request):
            return web.json_response({"error": "Wymagana autoryzacja zarządu."}, status=401)
        app_id_str = request.match_info.get("id")
        try:
            app_id = int(app_id_str)
        except (ValueError, TypeError):
            return web.json_response({"error": "Nieprawidłowe ID aplikacji."}, status=400)

        body = await request.json()
        raw_action = body.get("action", "").lower().strip()
        reason = body.get("reason", "").strip() or None

        if raw_action in ("accept", "approved", "accepted"):
            action = "accept"
        elif raw_action in ("reject", "rejected"):
            action = "reject"
        elif raw_action in ("delete", "deleted", "remove"):
            action = "delete"
        else:
            return web.json_response({"error": "Nieprawidłowa akcja (wymagane: accept, reject lub delete)."}, status=400)

        row = await self.bot.db.fetchone("SELECT * FROM applications WHERE id = ?", (app_id,))
        if not row:
            return web.json_response({"error": "Nie znaleziono aplikacji o podanym ID."}, status=404)

        if action == "delete":
            await self.bot.db.execute("DELETE FROM applications WHERE id = ?", (app_id,))
            guild = self._get_primary_guild()
            if guild:
                try:
                    from views.application_views import execute_application_decision_external
                    await execute_application_decision_external(
                        bot=self.bot,
                        guild=guild,
                        user_id=row["user_id"],
                        action="delete",
                        admin_name="Panel Zarządu",
                        admin_id=0
                    )
                except Exception as e:
                    logger.warning(f"Błąd powiadamiania kanału o usunięciu z panelu: {e}")
            return web.json_response({"success": True, "action": "delete", "deleted_id": app_id})

        guild = self._get_primary_guild()

        # Bezpośrednia aktualizacja rekordu w bazie danych
        await self.bot.db.execute(
            """
            UPDATE applications
            SET status = ?, decision_by = 0, decision_by_name = 'Panel Zarządu', decision_reason = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (action, reason, app_id)
        )

        res = {"success": True, "action": action, "applicant_found": False}
        if guild:
            from views.application_views import execute_application_decision_external
            res = await execute_application_decision_external(
                bot=self.bot,
                guild=guild,
                user_id=row["user_id"],
                action=action,
                admin_name="Panel Zarządu",
                admin_id=0,
                reason=reason
            )
        return web.json_response(res)

    async def _handle_api_application_delete(self, request: web.Request) -> web.Response:
        """Trwale usuwa zgłoszenie rekrutacyjne z bazy danych."""
        if not self._is_authenticated(request):
            return web.json_response({"error": "Wymagana autoryzacja zarządu."}, status=401)
        app_id_str = request.match_info.get("id")
        try:
            app_id = int(app_id_str)
        except (ValueError, TypeError):
            return web.json_response({"error": "Nieprawidłowe ID aplikacji."}, status=400)

        row = await self.bot.db.fetchone("SELECT * FROM applications WHERE id = ?", (app_id,))
        if not row:
            return web.json_response({"error": "Nie znaleziono podania o podanym ID."}, status=404)

        await self.bot.db.execute("DELETE FROM applications WHERE id = ?", (app_id,))

        guild = self._get_primary_guild()
        if guild:
            try:
                from views.application_views import execute_application_decision_external
                await execute_application_decision_external(
                    bot=self.bot,
                    guild=guild,
                    user_id=row["user_id"],
                    action="delete",
                    admin_name="Panel Zarządu",
                    admin_id=0
                )
            except Exception as e:
                logger.warning(f"Błąd edycji wiadomości po DELETE z panelu: {e}")

        logger.info(f"Podanie #{app_id} zostało usunięte z bazy danych przez panel webowy.")
        return web.json_response({"success": True, "deleted_id": app_id})

    async def _handle_api_application_bulk_delete(self, request: web.Request) -> web.Response:
        """Masowo usuwa podania rekrutacyjne z bazy danych."""
        if not self._is_authenticated(request):
            return web.json_response({"error": "Wymagana autoryzacja zarządu."}, status=401)

        body = await request.json()
        ids = body.get("ids", [])
        if not isinstance(ids, list) or not ids:
            return web.json_response({"error": "Brak listy ID do usunięcia."}, status=400)

        valid_ids = [int(i) for i in ids if str(i).isdigit()]
        if not valid_ids:
            return web.json_response({"error": "Brak poprawnych ID do usunięcia."}, status=400)

        placeholders = ",".join("?" for _ in valid_ids)
        await self.bot.db.execute(f"DELETE FROM applications WHERE id IN ({placeholders})", tuple(valid_ids))
        logger.info(f"Masowo usunięto {len(valid_ids)} podań z bazy danych (ID: {valid_ids}).")
        return web.json_response({"success": True, "deleted_count": len(valid_ids), "deleted_ids": valid_ids})

    # ─── MODUŁ 2: WYSZUKIWARKA PROJECTRPG ───

    async def _handle_api_projectrpg_lookup(self, request: web.Request) -> web.Response:
        """Wyszukuje profil gracza na forum i w grze MTA."""
        query = request.query.get("query", "").strip()
        if not query:
            return web.json_response({"error": "Podaj nick lub UID gracza do wyszukania."}, status=400)

        from cogs.projectrpg import _find_forum_profile_url, _scrape_forum_game_info, _get_ase_data
        forum_ref = await _find_forum_profile_url(query)
        game_info = None
        if forum_ref and forum_ref.get("profile_url"):
            game_info = await _scrape_forum_game_info(forum_ref["profile_url"])

        # Sprawdź czy gracz jest aktualnie online w MTA
        is_online_mta = False
        try:
            ase_data = await _get_ase_data()
            if ase_data and ase_data.get("players_list"):
                q_lower = query.lower()
                for p in ase_data["players_list"]:
                    if p.lower() == q_lower or (forum_ref and forum_ref.get("nick", "").lower() == p.lower()):
                        is_online_mta = True
                        break
        except Exception:
            pass

        return web.json_response({
            "query": query,
            "found": forum_ref is not None,
            "forum": forum_ref or {},
            "game_info": game_info or {},
            "online_mta": is_online_mta,
        })

    # ─── MODUŁ 3: SYSTEM KAR & DYSCYPLINY ───

    async def _handle_api_strikes(self, request: web.Request) -> web.Response:
        """Pobiera listę minusów i ostrzeżeń członków organizacji."""
        if not self._is_authenticated(request):
            return web.json_response({"error": "Wymagana autoryzacja zarządu."}, status=401)
        guild = self._get_primary_guild()
        guild_id = guild.id if guild else (getattr(config, "GUILD_ID", 0) or 0)

        # 1. Strikes (minusy)
        strike_rows = await self.bot.db.fetchall(
            "SELECT id, user_id, amount, reason, admin_id, created_at FROM strikes WHERE guild_id = ? ORDER BY id DESC LIMIT 100",
            (guild_id,)
        )
        strikes = []
        for r in strike_rows:
            m_name = f"Użytkownik {r['user_id']}"
            adm_name = f"Admin {r['admin_id']}" if r["admin_id"] else "Panel/System"
            avatar_url = ""
            if guild:
                m = guild.get_member(r["user_id"])
                if m:
                    m_name = m.display_name
                    avatar_url = m.display_avatar.url
                adm = guild.get_member(r["admin_id"]) if r["admin_id"] else None
                if adm: adm_name = adm.display_name
            strikes.append({
                "id": r["id"],
                "type": "strike",
                "user_id": str(r["user_id"]),
                "member_name": m_name,
                "avatar": avatar_url,
                "amount": r["amount"],
                "reason": r["reason"] or "Brak powodu",
                "admin_name": adm_name,
                "created_at": str(r["created_at"]) if r["created_at"] else "",
            })

        # 2. Warnings (ostrzeżenia)
        warn_rows = await self.bot.db.fetchall(
            "SELECT warning_id, user_id, reason, moderator_id, created_at FROM warnings WHERE guild_id = ? ORDER BY warning_id DESC LIMIT 100",
            (guild_id,)
        )
        warnings = []
        for r in warn_rows:
            m_name = f"Użytkownik {r['user_id']}"
            adm_name = f"Mod {r['moderator_id']}" if r["moderator_id"] else "System"
            avatar_url = ""
            if guild:
                m = guild.get_member(r["user_id"])
                if m:
                    m_name = m.display_name
                    avatar_url = m.display_avatar.url
                adm = guild.get_member(r["moderator_id"]) if r["moderator_id"] else None
                if adm: adm_name = adm.display_name
            warnings.append({
                "id": r["warning_id"],
                "type": "warning",
                "user_id": str(r["user_id"]),
                "member_name": m_name,
                "avatar": avatar_url,
                "reason": r["reason"] or "Brak powodu",
                "admin_name": adm_name,
                "created_at": str(r["created_at"]) if r["created_at"] else "",
            })

        return web.json_response({
            "strikes": strikes,
            "warnings": warnings,
            "total_strikes": len(strikes),
            "total_warnings": len(warnings),
        })

    async def _handle_api_strike_add(self, request: web.Request) -> web.Response:
        """Dodaje karę / minus / ostrzeżenie dla członka."""
        if not self._is_authenticated(request):
            return web.json_response({"error": "Wymagana autoryzacja zarządu."}, status=401)
        body = await request.json()
        user_id_str = body.get("user_id")
        penalty_type = body.get("type", "strike")
        amount = int(body.get("amount", 1))
        reason = body.get("reason", "").strip() or "Brak podanego powodu"

        try:
            user_id = int(user_id_str)
        except (ValueError, TypeError):
            return web.json_response({"error": "Nieprawidłowe ID użytkownika."}, status=400)

        guild = self._get_primary_guild()
        guild_id = guild.id if guild else (getattr(config, "GUILD_ID", 0) or 0)

        if penalty_type == "warning":
            await self.bot.db.execute(
                "INSERT INTO warnings (guild_id, user_id, moderator_id, reason) VALUES (?, ?, ?, ?)",
                (guild_id, user_id, 0, reason)
            )
        else:
            await self.bot.db.execute(
                "INSERT INTO strikes (guild_id, user_id, amount, reason, admin_id) VALUES (?, ?, ?, ?, ?)",
                (guild_id, user_id, amount, reason, 0)
            )

        logger.info(f"Nadano {penalty_type} dla {user_id} przez panel Web.")
        return web.json_response({"success": True})

    async def _handle_api_strike_delete(self, request: web.Request) -> web.Response:
        """Usuwa minus lub ostrzeżenie z bodu bota."""
        if not self._is_authenticated(request):
            return web.json_response({"error": "Wymagana autoryzacja zarządu."}, status=401)
        strike_id = request.match_info.get("id")
        penalty_type = request.query.get("type", "strike")

        if penalty_type == "warning":
            await self.bot.db.execute("DELETE FROM warnings WHERE warning_id = ?", (strike_id,))
        else:
            await self.bot.db.execute("DELETE FROM strikes WHERE id = ?", (strike_id,))

        return web.json_response({"success": True})

    # ─── MODUŁ 4: MONITOR WPISOWEGO ───

    async def _handle_api_fee_check(self, request: web.Request) -> web.Response:
        """Sprawdza członków z rangą Team77, którzy nie mają rangi Wpisowe."""
        if not self._is_authenticated(request):
            return web.json_response({"error": "Wymagana autoryzacja zarządu."}, status=401)
        guild = self._get_primary_guild()
        if not guild:
            return web.json_response({"unpaid": [], "paid": [], "stats": {"total_team77": 0, "unpaid_count": 0, "paid_count": 0}})

        team77_role = discord.utils.find(lambda r: r.name.lower() == "team77", guild.roles)
        wpisowe_role = discord.utils.find(lambda r: r.name.lower() == "wpisowe", guild.roles)

        unpaid = []
        paid = []

        if team77_role:
            for m in guild.members:
                if getattr(m, "bot", False) or team77_role not in m.roles:
                    continue
                has_wpisowe = (wpisowe_role in m.roles) if wpisowe_role else False
                item = {
                    "id": str(m.id),
                    "name": m.display_name,
                    "username": str(m),
                    "avatar": m.display_avatar.url,
                    "joined_at": m.joined_at.strftime("%Y-%m-%d") if m.joined_at else "Brak danych",
                }
                if has_wpisowe:
                    paid.append(item)
                else:
                    unpaid.append(item)

        return web.json_response({
            "unpaid": unpaid,
            "paid": paid,
            "has_wpisowe_role": wpisowe_role is not None,
            "has_team77_role": team77_role is not None,
            "stats": {
                "total_team77": len(unpaid) + len(paid),
                "unpaid_count": len(unpaid),
                "paid_count": len(paid),
            }
        })

    async def _handle_api_fee_mark_paid(self, request: web.Request) -> web.Response:
        """Nadaje rolę Wpisowe użytkownikowi po uregulowaniu wpłaty."""
        if not self._is_authenticated(request):
            return web.json_response({"error": "Wymagana autoryzacja zarządu."}, status=401)
        body = await request.json()
        user_id_str = body.get("user_id")
        try:
            user_id = int(user_id_str)
        except (ValueError, TypeError):
            return web.json_response({"error": "Nieprawidłowe ID użytkownika."}, status=400)

        guild = self._get_primary_guild()
        if not guild:
            return web.json_response({"error": "Brak serwera Discord."}, status=500)

        wpisowe_role = discord.utils.find(lambda r: r.name.lower() == "wpisowe", guild.roles)
        if not wpisowe_role:
            return web.json_response({"error": "Rola 'Wpisowe' nie istnieje na serwerze Discord. Utwórz ją w ustawieniach serwera."}, status=404)

        member = guild.get_member(user_id)
        if not member:
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                return web.json_response({"error": "Nie znaleziono użytkownika na serwerze."}, status=404)

        try:
            await member.add_roles(wpisowe_role, reason="Oznaczono wpisowe jako uregulowane przez panel Web")
            return web.json_response({"success": True, "name": member.display_name})
        except Exception as e:
            logger.error(f"Błąd nadawania roli Wpisowe: {e}")
            return web.json_response({"error": f"Błąd uprawnień Discorda: {e}"}, status=500)

    async def _handle_api_fee_remind(self, request: web.Request) -> web.Response:
        """Wysyła przypomnienie o wpisowym na wybrany kanał Discord."""
        if not self._is_authenticated(request):
            return web.json_response({"error": "Wymagana autoryzacja zarządu."}, status=401)
        body = await request.json()
        channel_id_str = body.get("channel_id")
        guild = self._get_primary_guild()
        if not guild:
            return web.json_response({"error": "Brak serwera Discord."}, status=500)

        try:
            channel_id = int(channel_id_str)
        except (ValueError, TypeError):
            return web.json_response({"error": "Wybierz kanał do wysłania przypomnienia."}, status=400)

        channel = guild.get_channel(channel_id)
        if not channel:
            return web.json_response({"error": "Nie znaleziono kanału na serwerze."}, status=404)

        team77_role = discord.utils.find(lambda r: r.name.lower() == "team77", guild.roles)
        wpisowe_role = discord.utils.find(lambda r: r.name.lower() == "wpisowe", guild.roles)
        if not team77_role:
            return web.json_response({"error": "Rola Team77 nie istnieje."}, status=404)

        unpaid_members = [m for m in team77_role.members if not (wpisowe_role and wpisowe_role in m.roles) and not m.bot]
        if not unpaid_members:
            return web.json_response({"success": True, "message": "Wszyscy członkowie Team77 posiadają już rolę Wpisowe!"})

        mentions = " ".join(m.mention for m in unpaid_members[:25])
        unpaid_list_str = "\n".join(f"• {m.mention} (`{m.display_name}`)" for m in unpaid_members[:20])
        if len(unpaid_members) > 20:
            unpaid_list_str += f"\n*...i jeszcze {len(unpaid_members) - 20} członków.*"

        embed = create_embed(
            title="💰 Przypomnienie o Wpisowym • Team77",
            description=(
                f"Przypominamy o konieczności wpłaty jednorazowego wpisowego (**3 000 PLN**) na konto organizacji w grze.\n\n"
                f"**Osoby oczekujące na wpisowe ({len(unpaid_members)}):**\n"
                f"{unpaid_list_str}\n\n"
                f"Po dokonaniu wpłaty zgłoś ją komendą `/wplata` na kanale wpłat!"
            ),
            color=config.WARNING_COLOR
        )
        msg = await channel.send(content=mentions, embed=embed)
        return web.json_response({"success": True, "count": len(unpaid_members), "jump_url": msg.jump_url})


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WebDashboardCog(bot))
