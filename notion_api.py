"""Notion APIクライアント(壁掛けKPIダッシュボード用) v2

APIバージョン 2025-09-03 のデータソースクエリを使用。
data_source_id は Notion MCP の fetch 結果 (collection://...) から確定済み。
"""

from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import requests

NOTION_VERSION = "2025-09-03"
BASE_URL = "https://api.notion.com/v1"

# ==== データソースID(確定値) ====
DS_CONDITION = "91791823-c8a5-42e3-9bc7-b2d9b88798d7"  # 💆‍♂️ コンディション記録
DS_RUNNING = "0d1fcf63-98df-4972-96b5-b95fee64116c"    # 🏃 ランニング記録
DS_DAILY_LOG = "d6f89947-fd07-470a-8e9d-74941ca111eb"  # 日次ログ
DS_THOUGHT = "3456e5b9-ef10-80b8-a92b-000b6480cc94"    # 思考記録
DS_TASK = "1c36e5b9-ef10-8192-9649-000b1cf955e8"       # ✅ タスク
DS_TIMEBUCKET = "1ca6e5b9-ef10-801e-aa7e-000b73320688"  # Time Bucket
DS_MEDITATION = "1f06e5b9-ef10-8039-b0b6-000b6fd9e5e2"  # 瞑想記録
DS_LEARNING = "44ad34d1-65b0-4085-b20a-9504379e3408"    # 学習記録(英語+Anki)
DS_TADOKU = "26f6e5b9-ef10-802c-bd4e-000b2ecbfa6a"      # 多読記録
DS_ACTION = "3526e5b9-ef10-800d-9067-000b606d65a1"      # アクション・問い
DS_HANSHO = "1b6ccdbb-90e1-40e9-92b6-31286c7c506a"      # 反証体験ログ
DS_MEAL = "c286a9de-3db6-45ee-b055-cb8c21620601"        # 🍽️ 食事記録


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def query(token: str, data_source_id: str, payload: dict) -> list[dict]:
    """データソースをクエリし、全ページをページネーションで取得する。"""
    url = f"{BASE_URL}/data_sources/{data_source_id}/query"
    payload = {"page_size": 100, **payload}
    results: list[dict] = []
    while True:
        res = requests.post(url, headers=_headers(token), json=payload, timeout=30)
        res.raise_for_status()
        data = res.json()
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        payload["start_cursor"] = data["next_cursor"]
    return results


def _date_filter(prop: str, since: dt.date) -> dict:
    return {
        "filter": {"property": prop, "date": {"on_or_after": since.isoformat()}},
        "sorts": [{"property": prop, "direction": "ascending"}],
    }


# ==== プロパティ値の取り出しヘルパー ====

def prop_number(page: dict, name: str):
    return page.get("properties", {}).get(name, {}).get("number")


def prop_select(page: dict, name: str):
    sel = page.get("properties", {}).get(name, {}).get("select")
    return sel.get("name") if sel else None


def prop_status(page: dict, name: str):
    stt = page.get("properties", {}).get(name, {}).get("status")
    return stt.get("name") if stt else None


def prop_date(page: dict, name: str):
    d = page.get("properties", {}).get(name, {}).get("date")
    return d.get("start") if d else None


def prop_checkbox(page: dict, name: str) -> bool:
    return bool(page.get("properties", {}).get(name, {}).get("checkbox"))


def prop_rich_text(page: dict, name: str) -> str:
    parts = page.get("properties", {}).get(name, {}).get("rich_text", [])
    return "".join(t.get("plain_text", "") for t in parts)


def prop_title(page: dict, name: str) -> str:
    parts = page.get("properties", {}).get(name, {}).get("title", [])
    return "".join(t.get("plain_text", "") for t in parts)


def prop_multi_select(page: dict, name: str) -> list[str]:
    opts = page.get("properties", {}).get(name, {}).get("multi_select", [])
    return [o.get("name") for o in opts]


def prop_has_relation(page: dict, name: str) -> bool:
    rel = page.get("properties", {}).get(name, {})
    return bool(rel.get("relation")) or bool(rel.get("has_more"))


# ==== ダッシュボード用の一括取得 ====

RUNNING_SINCE = dt.date(2026, 1, 1)  # 累計は今年全体を対象


def fetch_all(token: str, days: int = 30) -> dict[str, list[dict]]:
    """5データソースを並列取得する。

    condition/daily_log: 直近 days 日
    running: 100km目標期間の起点から全件
    thoughts: ステータスが 未処理 or 再処理 のもの(在庫)
    tasks: 今日生成分 + 今週期限のMust
    """
    today = dt.date.today()
    since_recent = today - dt.timedelta(days=days)
    week_end = today + dt.timedelta(days=(6 - today.weekday()))  # 今週の日曜
    week_start = today - dt.timedelta(days=today.weekday())      # 今週の月曜
    today_jst_start = f"{today.isoformat()}T00:00:00+09:00"

    jobs: dict[str, tuple[str, dict]] = {
        "condition": (DS_CONDITION, _date_filter("日付", since_recent)),
        "running": (DS_RUNNING, _date_filter("日時", RUNNING_SINCE)),
        "daily_log": (DS_DAILY_LOG, _date_filter("日付", since_recent)),
        "thoughts_open": (DS_THOUGHT, {"filter": {
            "property": "ステータス", "select": {"does_not_equal": "完了"}}}),
        "tasks_today": (DS_TASK, {"filter": {
            "timestamp": "created_time",
            "created_time": {"on_or_after": today_jst_start},
        }}),
        "tasks_must_due": (DS_TASK, {"filter": {"and": [
            {"property": "優先度", "select": {"equals": "Must"}},
            {"property": "締め切り", "date": {"on_or_before": week_end.isoformat()}},
        ]}}),
        "timebucket": (DS_TIMEBUCKET, {}),  # 全件(件数は少ない前提)
        "learning": (DS_LEARNING, _date_filter("日付", today - dt.timedelta(days=84))),
        "meals": (DS_MEAL, _date_filter("日付", since_recent)),
        "thoughts_month": (DS_THOUGHT, {"filter": {
            "timestamp": "created_time",
            "created_time": {"on_or_after": f"{today.replace(day=1).isoformat()}T00:00:00+09:00"},
        }}),
        "thoughts_new_week": (DS_THOUGHT, {"filter": {
            "timestamp": "created_time",
            "created_time": {"on_or_after": f"{week_start.isoformat()}T00:00:00+09:00"},
        }}),
        "thoughts_done_week": (DS_THOUGHT, {"filter": {"and": [
            {"property": "ステータス", "select": {"equals": "完了"}},
            {"timestamp": "last_edited_time",
             "last_edited_time": {"on_or_after": f"{week_start.isoformat()}T00:00:00+09:00"}},
        ]}}),
        "actions_all": (DS_ACTION, {}),
    }
    out: dict[str, list[dict]] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {k: ex.submit(query, token, ds, payload) for k, (ds, payload) in jobs.items()}
        for k, fut in futures.items():
            try:
                out[k] = fut.result()
            except requests.HTTPError as e:
                out[k] = []
                code = e.response.status_code if e.response is not None else "?"
                errors.append(f"{k} (HTTP {code}: DBがインテグレーションに接続されていない可能性)")
    out["_errors"] = errors  # type: ignore[assignment]
    return out


def fetch_alltime(token: str) -> dict[str, list[dict]]:
    """通算カウント・最長ストリーク・成長ログ用の全期間取得。

    呼び出し側で長め(1時間)にキャッシュする前提。
    """
    since30 = dt.date.today() - dt.timedelta(days=30)
    jobs = {
        "condition_all": (DS_CONDITION, {"sorts": [{"property": "日付", "direction": "ascending"}]}),
        "daily_all": (DS_DAILY_LOG, {"sorts": [{"property": "日付", "direction": "ascending"}]}),
        "running_all": (DS_RUNNING, {"sorts": [{"property": "日時", "direction": "ascending"}]}),
        "meditation_all": (DS_MEDITATION, {"sorts": [{"property": "日付", "direction": "ascending"}]}),
        "tadoku_all": (DS_TADOKU, {}),
        "hansho_all": (DS_HANSHO, {"sorts": [{"property": "日付", "direction": "ascending"}]}),
        "tasks_30d": (DS_TASK, {"filter": {
            "timestamp": "created_time",
            "created_time": {"on_or_after": f"{since30.isoformat()}T00:00:00+09:00"},
        }}),
    }
    out: dict[str, list[dict]] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {k: ex.submit(query, token, ds, payload) for k, (ds, payload) in jobs.items()}
        for k, fut in futures.items():
            try:
                out[k] = fut.result()
            except requests.HTTPError as e:
                out[k] = []
                code = e.response.status_code if e.response is not None else "?"
                errors.append(f"{k} (HTTP {code})")
    out["_errors"] = errors  # type: ignore[assignment]
    return out
