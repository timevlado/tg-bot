"""Статистика НХЛ для ассистента клуба: загрузка матчей с официального NHL API в Postgres и запросы.

Источник: api-web.nhle.com (публичный, без ключа).
  /v1/score/{YYYY-MM-DD}                 — матчи дня: команды, счёт, статус
  /v1/gamecenter/{id}/boxscore           — броски в створ (если их нет в /score)
"""
import asyncio
import logging
import os
from datetime import date, datetime, timedelta, timezone

import httpx
import psycopg2

log = logging.getLogger("nhl")

DATABASE_URL = os.environ.get("DATABASE_URL")
BASE = "https://api-web.nhle.com/v1"
MSK = timedelta(hours=3)
FINAL_STATES = ("OFF", "FINAL")

TEAMS = {
    "ANA": "Анахайм", "BOS": "Бостон", "BUF": "Баффало", "CGY": "Калгари",
    "CAR": "Каролина", "CHI": "Чикаго", "COL": "Колорадо", "CBJ": "Коламбус",
    "DAL": "Даллас", "DET": "Детройт", "EDM": "Эдмонтон", "FLA": "Флорида",
    "LAK": "Лос-Анджелес", "MIN": "Миннесота", "MTL": "Монреаль", "NSH": "Нэшвилл",
    "NJD": "Нью-Джерси", "NYI": "Айлендерс", "NYR": "Рейнджерс", "OTT": "Оттава",
    "PHI": "Филадельфия", "PIT": "Питтсбург", "SJS": "Сан-Хосе", "SEA": "Сиэтл",
    "STL": "Сент-Луис", "TBL": "Тампа", "TOR": "Торонто", "UTA": "Юта",
    "VAN": "Ванкувер", "VGK": "Вегас", "WSH": "Вашингтон", "WPG": "Виннипег",
}


# ===== БАЗА =====
def _db(query, params=(), fetch=None, many=False):
    conn = psycopg2.connect(DATABASE_URL)
    try:
        cur = conn.cursor()
        if many:
            cur.executemany(query, params)
        else:
            cur.execute(query, params)
        result = None
        if fetch == "one":
            result = cur.fetchone()
        elif fetch == "all":
            result = cur.fetchall()
        conn.commit()
        cur.close()
        return result
    finally:
        conn.close()


def init_db():
    _db("""
        CREATE TABLE IF NOT EXISTS nhl_games (
            game_id      BIGINT PRIMARY KEY,
            season       INT,
            game_type    INT,
            game_date    DATE,
            start_utc    TIMESTAMPTZ,
            home         TEXT,
            away         TEXT,
            home_goals   INT,
            away_goals   INT,
            home_sog     INT,
            away_sog     INT,
            home_goals_reg INT,
            away_goals_reg INT,
            last_period  TEXT,
            state        TEXT,
            updated_at   TIMESTAMPTZ DEFAULT NOW()
        )""")
    _db("CREATE INDEX IF NOT EXISTS nhl_games_home ON nhl_games (season, home)")
    _db("CREATE INDEX IF NOT EXISTS nhl_games_away ON nhl_games (season, away)")
    _db("CREATE INDEX IF NOT EXISTS nhl_games_date ON nhl_games (game_date)")
    _db("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")


def get_setting(key, default=None):
    row = _db("SELECT value FROM settings WHERE key=%s", (key,), fetch="one")
    return row[0] if row else default


def set_setting(key, value):
    _db("INSERT INTO settings (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, str(value)))


# ===== СЕЗОНЫ =====
def season_id(start_year):
    return start_year * 10000 + start_year + 1


def current_season(today=None):
    today = today or datetime.now(timezone.utc).date()
    start = today.year if today.month >= 9 else today.year - 1
    return season_id(start)


def resolve_season(value):
    cur = current_season()
    if value in (None, "", "current"):
        return cur
    if value == "previous":
        return cur - 10001
    try:
        return int(value)
    except (TypeError, ValueError):
        return cur


# ===== ЗАГРУЗКА С NHL API =====
async def _get_json(client, url):
    for attempt in range(3):
        try:
            r = await client.get(url, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
            log.warning("NHL API %s → %s", url, r.status_code)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("NHL API %s → %s", url, e)
        await asyncio.sleep(2 * (attempt + 1))
    return None


def reg_score(hg, ag, last_period):
    """Счёт основного времени. В ОТ забивают ровно один гол, буллиты дают победителю +1 —
    значит, при ОТ/Б у победителя минус один."""
    if hg is None or ag is None:
        return None, None
    if last_period in ("OT", "SO") and hg != ag:
        return (hg - 1, ag) if hg > ag else (hg, ag - 1)
    return hg, ag


def _parse_start(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _sync_date(client, d, complete_ids, sem):
    async with sem:
        data = await _get_json(client, f"{BASE}/score/{d.isoformat()}")
    if not data:
        return []
    rows = []
    for g in data.get("games", []):
        game_type = g.get("gameType")
        if game_type not in (2, 3):  # только регулярка и плей-офф
            continue
        gid = g.get("id")
        home, away = g.get("homeTeam") or {}, g.get("awayTeam") or {}
        state = g.get("gameState") or ""
        final = state in FINAL_STATES
        if final and gid in complete_ids:
            continue  # уже лежит в базе целиком
        hs, as_ = home.get("sog"), away.get("sog")
        if final and (hs is None or as_ is None):
            async with sem:
                box = await _get_json(client, f"{BASE}/gamecenter/{gid}/boxscore")
            if box:
                hs = (box.get("homeTeam") or {}).get("sog")
                as_ = (box.get("awayTeam") or {}).get("sog")
        last_period = ((g.get("gameOutcome") or {}).get("lastPeriodType")
                       or (g.get("periodDescriptor") or {}).get("periodType"))
        hg, ag = home.get("score"), away.get("score")
        hr, ar = reg_score(hg, ag, last_period) if final else (None, None)
        rows.append((
            gid, g.get("season"), game_type, g.get("gameDate") or d.isoformat(),
            _parse_start(g.get("startTimeUTC")), home.get("abbrev"), away.get("abbrev"),
            hg if final else None, ag if final else None,
            hs if final else None, as_ if final else None,
            hr, ar, last_period if final else None, state,
        ))
    return rows


UPSERT = """
    INSERT INTO nhl_games (game_id, season, game_type, game_date, start_utc, home, away,
                           home_goals, away_goals, home_sog, away_sog, home_goals_reg, away_goals_reg,
                           last_period, state, updated_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
    ON CONFLICT (game_id) DO UPDATE SET
        season=EXCLUDED.season, game_type=EXCLUDED.game_type, game_date=EXCLUDED.game_date,
        start_utc=EXCLUDED.start_utc, home=EXCLUDED.home, away=EXCLUDED.away,
        home_goals=EXCLUDED.home_goals, away_goals=EXCLUDED.away_goals,
        home_sog=EXCLUDED.home_sog, away_sog=EXCLUDED.away_sog,
        home_goals_reg=EXCLUDED.home_goals_reg, away_goals_reg=EXCLUDED.away_goals_reg,
        last_period=EXCLUDED.last_period, state=EXCLUDED.state, updated_at=NOW()
"""


async def sync_range(start, end):
    """Загрузить все матчи с start по end включительно. Возвращает число обновлённых матчей."""
    complete = await asyncio.to_thread(
        _db, "SELECT game_id FROM nhl_games WHERE state IN ('OFF','FINAL') AND home_sog IS NOT NULL",
        (), "all")
    complete_ids = {r[0] for r in complete or []}
    days = []
    d = start
    while d <= end:
        if d.month not in (7, 8, 9):  # межсезонье — матчей нет
            days.append(d)
        d += timedelta(days=1)
    sem = asyncio.Semaphore(4)
    total = 0
    headers = {"User-Agent": "club-svoi-assistant/1.0"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        for i in range(0, len(days), 20):  # пачками, чтобы не долбить API
            batch = await asyncio.gather(*(_sync_date(client, x, complete_ids, sem) for x in days[i:i + 20]))
            rows = [r for day_rows in batch for r in day_rows]
            if rows:
                await asyncio.to_thread(_db, UPSERT, rows, None, True)
                total += len(rows)
    return total


async def sync_recent():
    today = datetime.now(timezone.utc).date()
    return await sync_range(today - timedelta(days=2), today + timedelta(days=3))


async def backfill_if_needed():
    """Первый запуск: прошлый сезон + текущий. Один раз, дальше только свежие дни."""
    season = current_season()
    key = f"nhl_backfill_{season}"
    if get_setting(key) == "1":
        return 0
    start_year = season // 10000 - 1
    n = await sync_range(date(start_year, 10, 1), datetime.now(timezone.utc).date() + timedelta(days=3))
    set_setting(key, "1")
    return n


# ===== ЗАПРОСЫ ДЛЯ АССИСТЕНТА =====
GAME_COLS = ("game_id, game_date, home, away, home_goals, away_goals, home_sog, away_sog, last_period, "
             "home_goals_reg, away_goals_reg")


def _final_games(team, season, venue="all", last_n=None, game_type=2):
    where = ["season=%s", "game_type=%s", "state IN ('OFF','FINAL')", "home_goals IS NOT NULL"]
    params = [season, game_type]
    if venue == "home":
        where.append("home=%s")
        params.append(team)
    elif venue == "away":
        where.append("away=%s")
        params.append(team)
    else:
        where.append("(home=%s OR away=%s)")
        params += [team, team]
    q = f"SELECT {GAME_COLS} FROM nhl_games WHERE {' AND '.join(where)} ORDER BY game_date DESC, game_id DESC"
    if last_n:
        q += " LIMIT %s"
        params.append(int(last_n))
    return _db(q, tuple(params), fetch="all") or []


def _perspective(row, team):
    _, gdate, home, away, hg, ag, hs, as_, lp, hr, ar = row
    is_home = home == team
    gf, ga = (hg, ag) if is_home else (ag, hg)
    if hr is None:
        hr, ar = reg_score(hg, ag, lp)
    rf, ra = (hr, ar) if is_home else (ar, hr)
    sf, sa = (hs, as_) if is_home else (as_, hs)
    return {
        "date": gdate.isoformat() if hasattr(gdate, "isoformat") else str(gdate),
        "opp": away if is_home else home, "home": is_home,
        "gf": gf, "ga": ga, "rf": rf, "ra": ra, "sf": sf, "sa": sa,
        "extra": lp if lp in ("OT", "SO") else None,
    }


def _avg(values):
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _summary(games):
    n = len(games)
    if not n:
        return {"games": 0}
    wins = sum(1 for g in games if g["gf"] > g["ga"])
    ot_losses = sum(1 for g in games if g["gf"] < g["ga"] and g["extra"])
    totals = [g["rf"] + g["ra"] for g in games]
    shot_totals = [g["sf"] + g["sa"] for g in games if g["sf"] is not None and g["sa"] is not None]
    return {
        "games": n,
        "record": f"{wins}-{n - wins - ot_losses}-{ot_losses} (В-П-ПОТ/Б)",
        "goals_for_avg": _avg(g["rf"] for g in games),
        "goals_against_avg": _avg(g["ra"] for g in games),
        "total_goals_avg": _avg(totals),
        "total_over_5_5": f"{sum(1 for t in totals if t > 5.5)} из {n}",
        "total_over_6_5": f"{sum(1 for t in totals if t > 6.5)} из {n}",
        "sog_for_avg": _avg(g["sf"] for g in games),
        "sog_against_avg": _avg(g["sa"] for g in games),
        "sog_total_avg": _avg(shot_totals),
        "sog_for_min_max": _minmax(g["sf"] for g in games),
        "went_to_ot_or_so": sum(1 for g in games if g["extra"]),
        "note": "голы и тоталы — за основное время (60 минут), как в линии на основное время",
        "total_goals_with_ot_avg": _avg(g["gf"] + g["ga"] for g in games),
    }


def _minmax(values):
    vals = [v for v in values if v is not None]
    return f"{min(vals)}–{max(vals)}" if vals else None


def _fmt_game(g):
    where = "дома" if g["home"] else "в гостях"
    extra = f" ({g['extra']})" if g["extra"] else ""
    sog = f", броски {g['sf']}:{g['sa']}" if g["sf"] is not None else ""
    return f"{g['date']} {where} vs {g['opp']}: {g['gf']}:{g['ga']}{extra}{sog}"


def check_team(team):
    team = (team or "").upper().strip()
    if team not in TEAMS:
        raise ValueError(f"Неизвестная команда {team!r}. Коды: {', '.join(TEAMS)}")
    return team


def team_stats(team, venue="all", last_n=None, season=None):
    team = check_team(team)
    sid = resolve_season(season)
    games = [_perspective(r, team) for r in _final_games(team, sid, venue, last_n)]
    return {
        "team": f"{TEAMS[team]} ({team})", "season": sid,
        "venue": {"home": "дома", "away": "в гостях"}.get(venue, "все матчи"),
        "sample": f"последние {last_n}" if last_n else "весь сезон (регулярка)",
        **_summary(games),
        "last_5": [_fmt_game(g) for g in games[:5]],
    }


def recent_games(team, n=10, venue="all"):
    team = check_team(team)
    n = max(1, min(int(n or 10), 25))
    games = [_perspective(r, team) for r in _final_games(team, current_season(), venue, n)]
    return {"team": f"{TEAMS[team]} ({team})", "games": [_fmt_game(g) for g in games]}


def head_to_head(team1, team2, limit=10):
    t1, t2 = check_team(team1), check_team(team2)
    rows = _db(
        f"SELECT {GAME_COLS}, season FROM nhl_games WHERE state IN ('OFF','FINAL') AND home_goals IS NOT NULL "
        "AND ((home=%s AND away=%s) OR (home=%s AND away=%s)) ORDER BY game_date DESC LIMIT %s",
        (t1, t2, t2, t1, max(1, min(int(limit or 10), 20))), fetch="all") or []
    games = [_perspective(r[:11], t1) for r in rows]
    return {
        "teams": f"{TEAMS[t1]} — {TEAMS[t2]}",
        "perspective": TEAMS[t1],
        "games": [_fmt_game(g) for g in games],
        **({k: v for k, v in _summary(games).items() if k != "record"} if games else {"games_found": 0}),
        "note": "в базе текущий и прошлый сезоны",
    }


def _fmt_msk(start):
    if not start:
        return "время неизвестно"
    return (start.astimezone(timezone.utc) + MSK).strftime("%d.%m %H:%M МСК")


def _fmt_sched(row):
    _, gdate, home, away, hg, ag, hs, as_, lp, _hr, _ar, start, state = row
    line = f"{TEAMS.get(away, away)} @ {TEAMS.get(home, home)} — {_fmt_msk(start)}"
    if state in FINAL_STATES and hg is not None:
        extra = f" ({lp})" if lp in ("OT", "SO") else ""
        sog = f", броски {as_}:{hs}" if hs is not None else ""
        line += f" — итог {ag}:{hg}{extra}{sog}"
    elif state in ("LIVE", "CRIT"):
        line += " — идёт"
    return line


def games_on_date(day):
    rows = _db(f"SELECT {GAME_COLS}, start_utc, state FROM nhl_games WHERE game_date=%s ORDER BY start_utc",
               (day,), fetch="all") or []
    return {"date_nhl": day, "note": "дата по Северной Америке; время — московское",
            "games": [_fmt_sched(r) for r in rows] or ["матчей нет или ещё не загружены"]}


def upcoming_games(team=None, limit=8):
    params = []
    q = (f"SELECT {GAME_COLS}, start_utc, state FROM nhl_games "
         "WHERE state NOT IN ('OFF','FINAL') AND start_utc > NOW() - INTERVAL '4 hours'")
    if team:
        team = check_team(team)
        q += " AND (home=%s OR away=%s)"
        params += [team, team]
    q += " ORDER BY start_utc LIMIT %s"
    params.append(max(1, min(int(limit or 8), 20)))
    rows = _db(q, tuple(params), fetch="all") or []
    return {"games": [_fmt_sched(r) for r in rows] or ["ближайших матчей в базе нет"]}


METRICS = {
    "sog_for": ("sog_for_avg", True), "sog_against": ("sog_against_avg", True),
    "sog_total": ("sog_total_avg", True), "goals_for": ("goals_for_avg", True),
    "goals_against": ("goals_against_avg", True), "goals_total": ("total_goals_avg", True),
}


def league_ranking(metric="sog_for", venue="all", last_n=None):
    if metric not in METRICS:
        raise ValueError(f"metric: {', '.join(METRICS)}")
    key, _ = METRICS[metric]
    table = []
    for team in TEAMS:
        games = [_perspective(r, team) for r in _final_games(team, current_season(), venue, last_n)]
        s = _summary(games)
        if s.get(key) is not None:
            table.append((s[key], team, s["games"]))
    table.sort(reverse=True)
    return {
        "metric": metric, "venue": venue, "sample": f"последние {last_n}" if last_n else "весь сезон",
        "ranking_desc": [f"{i}. {TEAMS[t]} — {v} ({g} м.)" for i, (v, t, g) in enumerate(table, 1)],
    }


def db_status():
    row = _db("SELECT COUNT(*), COUNT(*) FILTER (WHERE state IN ('OFF','FINAL')), "
              "COUNT(*) FILTER (WHERE state IN ('OFF','FINAL') AND home_sog IS NULL), MAX(updated_at) "
              "FROM nhl_games WHERE season=%s", (current_season(),), fetch="one")
    return {"season": current_season(), "games_in_db": row[0], "finished": row[1],
            "finished_without_shots": row[2], "last_update": str(row[3])}
