"""週次レビュー自動生成(日曜7:00 JST)

今週の数字と成長の素材をNotionから集め、振り返りページを自動作成する。
人間はスマホでこのページを開き、4つの問いに答えるだけ。

必要な環境変数: NOTION_TOKEN
必要な設定: PARENT_PAGE_ID(レビューページを作る親ページのID)
"""

from __future__ import annotations

import datetime as dt
import os
import sys

import requests

# ==== 設定 ====
DS_REVIEW = "160f1307-aead-48e0-88ab-042ced358cf4"  # 週次レビューDB(自動生成の溜め先)

JST = dt.timezone(dt.timedelta(hours=9))
NOTION_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"

DS = {
    "learning": "44ad34d1-65b0-4085-b20a-9504379e3408",
    "running": "0d1fcf63-98df-4972-96b5-b95fee64116c",
    "daily_log": "d6f89947-fd07-470a-8e9d-74941ca111eb",
    "meditation": "1f06e5b9-ef10-8039-b0b6-000b6fd9e5e2",
    "tasks": "1c36e5b9-ef10-8192-9649-000b1cf955e8",
    "thoughts": "3456e5b9-ef10-80b8-a92b-000b6480cc94",
    "tadoku": "26f6e5b9-ef10-802c-bd4e-000b2ecbfa6a",
}
WEEKLY_EN_MIN = 420.0
GOAL_KM, GOAL_START, GOAL_END = 100.0, dt.date(2026, 1, 1), dt.date(2026, 12, 31)
TASK_OPEN = {"未着手", "進行中", "中断"}


def headers():
    return {"Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
            "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"}


def query(ds_id: str, payload: dict) -> list[dict]:
    payload = {"page_size": 100, **payload}
    out = []
    while True:
        r = requests.post(f"{NOTION_BASE}/data_sources/{ds_id}/query",
                          headers=headers(), json=payload, timeout=60)
        r.raise_for_status()
        d = r.json()
        out.extend(d.get("results", []))
        if not d.get("has_more"):
            return out
        payload["start_cursor"] = d["next_cursor"]


def p_num(pg, name):
    return pg["properties"].get(name, {}).get("number")


def p_sel(pg, name):
    s = pg["properties"].get(name, {}).get("select")
    return s.get("name") if s else None


def p_status(pg, name):
    s = pg["properties"].get(name, {}).get("status")
    return s.get("name") if s else None


def p_text(pg, name):
    return "".join(t.get("plain_text", "")
                   for t in pg["properties"].get(name, {}).get("rich_text", []))


def p_title(pg, name):
    return "".join(t.get("plain_text", "")
                   for t in pg["properties"].get(name, {}).get("title", []))


def p_date(pg, name):
    d = pg["properties"].get(name, {}).get("date")
    return d.get("start") if d else None


def minutes_of(pg) -> float:
    n = p_num(pg, "時間_分")
    if n is not None:
        return n
    d = pg["properties"].get("計測", {}).get("date") or {}
    if d.get("start") and d.get("end"):
        try:
            t0 = dt.datetime.fromisoformat(d["start"])
            t1 = dt.datetime.fromisoformat(d["end"])
            m = (t1 - t0).total_seconds() / 60
            return m if 0 < m < 1440 else 0.0
        except ValueError:
            return 0.0
    return 0.0


# ==== ブロック生成ヘルパー ====
def h2(text):
    return {"heading_2": {"rich_text": [{"text": {"content": text}}]}}


def para(text, color="default"):
    return {"paragraph": {"rich_text": [{"text": {"content": text[:1900]}}],
                          "color": color}}


def bullet(text):
    return {"bulleted_list_item": {"rich_text": [{"text": {"content": text[:1900]}}]}}


def quote(text):
    return {"quote": {"rich_text": [{"text": {"content": text[:1900]}}]}}


def todo(text):
    return {"to_do": {"rich_text": [{"text": {"content": text[:1900]}}],
                      "checked": False}}


def divider():
    return {"divider": {}}


def main():
    today = dt.datetime.now(JST).date()
    week_start = today - dt.timedelta(days=today.weekday())  # 月曜
    week_end = week_start + dt.timedelta(days=6)
    wk_filter = lambda prop: {"filter": {
        "property": prop, "date": {"on_or_after": week_start.isoformat()}}}

    blocks = [para(f"対象週: {week_start} 〜 {week_end}(自動生成)", "gray")]

    # ---- 数字 ----
    nums = []
    try:
        learn = query(DS["learning"], wk_filter("日付"))
        total_min = sum(minutes_of(p) for p in learn)
        by_type = {}
        for p in learn:
            by_type[p_sel(p, "種別") or "その他"] = \
                by_type.get(p_sel(p, "種別") or "その他", 0) + minutes_of(p)
        parts = " / ".join(f"{k} {v:.0f}分" for k, v in by_type.items() if v)
        touch_days = len({p_date(p, "日付")[:10] for p in learn if p_date(p, "日付")})
        nums.append(f"🇬🇧 英語 {total_min / 60:.1f}h / 7h "
                    f"({total_min / WEEKLY_EN_MIN * 100:.0f}%) ・ 接触 {touch_days}日"
                    + (f" ・ 内訳: {parts}" if parts else ""))
        growth_cmts = [(p_date(p, "日付")[:10], p_sel(p, "種別") or "",
                        p_text(p, "成長コメント"))
                       for p in learn if p_text(p, "成長コメント").strip()]
    except Exception as e:
        nums.append(f"🇬🇧 英語: 取得失敗 ({e})")
        growth_cmts = []

    try:
        runs = query(DS["running"], {"filter": {
            "property": "日時", "date": {"on_or_after": GOAL_START.isoformat()}}})
        total_km = sum(p_num(p, "距離_km") or 0 for p in runs)
        wk_km = sum(p_num(p, "距離_km") or 0 for p in runs
                    if (p_date(p, "日時") or "")[:10] >= week_start.isoformat())
        elapsed = (today - GOAL_START).days + 1
        on_pace = GOAL_KM * elapsed / ((GOAL_END - GOAL_START).days + 1)
        nums.append(f"🏃 ラン 今週 {wk_km:.1f}km ・ 年間 {total_km:.1f}km "
                    f"(計画比 {total_km - on_pace:+.1f}km)")
    except Exception as e:
        nums.append(f"🏃 ラン: 取得失敗 ({e})")

    try:
        logs = query(DS["daily_log"], wk_filter("日付"))
        meds = query(DS["meditation"], wk_filter("日付"))
        med_min = sum(p_num(p, "時間") or 0 for p in meds)
        nums.append(f"✅ 習慣 日次ログ {len(logs)}/7日 ・ "
                    f"瞑想 {len({p_date(p, '日付')[:10] for p in meds})}日 {med_min:.0f}分")
        growth_logs = []
        for p in logs:
            for k in ("今日成長したこと1", "今日成長したこと2", "今日成長したこと3"):
                t = p_text(p, k).strip()
                if t:
                    growth_logs.append((p_date(p, "日付")[:10], t))
    except Exception as e:
        nums.append(f"✅ 習慣: 取得失敗 ({e})")
        growth_logs = []

    try:
        tasks = query(DS["tasks"], {"filter": {"and": [
            {"property": "実行日時",
             "date": {"on_or_after": f"{week_start.isoformat()}T00:00:00+09:00"}},
        ]}})
        n_all = len(tasks)
        n_done = sum(1 for p in tasks if p_status(p, "ステータス") not in TASK_OPEN)
        pomo = sum(p_num(p, "ポモ数") or 0 for p in tasks)
        must_done = [p_title(p, "名前") for p in tasks
                     if p_sel(p, "優先度") == "Must"
                     and p_status(p, "ステータス") == "完了"]
        nums.append(f"📋 タスク {n_done}/{n_all} "
                    f"({n_done / n_all * 100:.0f}%)" if n_all else "📋 タスク 0件"
                    )
        if pomo:
            nums.append(f"🍅 ポモドーロ {pomo:.0f}回")
    except Exception as e:
        nums.append(f"📋 タスク: 取得失敗 ({e})")
        must_done = []

    try:
        new_th = query(DS["thoughts"], {"filter": {
            "timestamp": "created_time",
            "created_time": {"on_or_after": f"{week_start.isoformat()}T00:00:00+09:00"}}})
        done_th = query(DS["thoughts"], {"filter": {"and": [
            {"property": "ステータス", "select": {"equals": "完了"}},
            {"timestamp": "last_edited_time",
             "last_edited_time": {"on_or_after": f"{week_start.isoformat()}T00:00:00+09:00"}}]}})
        nums.append(f"💭 思考 +{len(new_th)}起票 / ✓{len(done_th)}完了(推定)")
    except Exception as e:
        nums.append(f"💭 思考: 取得失敗 ({e})")

    blocks.append(h2("📊 今週の数字"))
    blocks += [bullet(n) for n in nums]

    # ---- 成長の素材 ----
    blocks.append(divider())
    blocks.append(h2("🌱 今週の成長の素材(自動転記)"))
    material = False
    for d, typ, c in growth_cmts[:5]:
        blocks.append(bullet(f"⭐ {d} ({typ}) {c}"))
        material = True
    for d, t in growth_logs[:8]:
        blocks.append(bullet(f"🌱 {d} {t}"))
        material = True
    try:
        books = query(DS["tadoku"], {"filter": {
            "property": "読了", "date": {"on_or_after": week_start.isoformat()}}})
        for p in books:
            blocks.append(bullet(f"📚 読了「{p_title(p, '名前')}」 "
                                 f"{p_text(p, '感想')[:100]}"))
            material = True
    except Exception:
        pass
    for name in must_done[:5]:
        blocks.append(bullet(f"🔴 Must完了: {name}"))
        material = True
    if not material:
        blocks.append(para("(今週の素材はまだ少なめ。それも記録)", "gray"))

    # ---- 4つの問い ----
    blocks.append(divider())
    blocks.append(h2("✍️ 振り返り(各1〜2行で)"))
    for q in [
        "🌱 上の素材を読んで——今週の自分に、友人としてかける言葉は?",
        "✅ 続けたいこと1つ(うまくいった行動・仕組み)",
        "🔧 つまずき1つ + 仕組みでどう防ぐ?",
    ]:
        blocks.append(quote(q))
        blocks.append(para(""))
    blocks.append(h2("🎯 来週のこれだけは1つ"))
    blocks.append(todo(""))

    # ---- 隔週PCメンテのリマインド ----
    if week_start.isocalendar().week % 2 == 0:
        blocks.append(divider())
        blocks.append(para("🖥️ 今週はPCメンテ週: /weekly-review でトリアージと棚卸しを",
                           "orange"))

    # ---- ページ作成 ----
    r = requests.post(f"{NOTION_BASE}/pages", headers=headers(), json={
        "parent": {"type": "data_source_id", "data_source_id": DS_REVIEW},
        "properties": {
            "名前": {"title": [
                {"text": {"content": f"週次レビュー {week_start.strftime('%Y-%m-%d')}"}}]},
            "週": {"date": {"start": week_start.isoformat()}},
        },
        "children": blocks[:100],
    }, timeout=60)
    r.raise_for_status()
    print(f"作成: 週次レビュー {week_start} → {r.json().get('url')}")


if __name__ == "__main__":
    main()
