"""壁掛けKPIダッシュボード (Streamlit) v3 — 詳細ページ追加版

ナビ構成:
  今日        … 壁掛け用3秒ビュー(v2と同一)
  コンディション … バイオメトリクス詳細(30日トレンド+信号履歴)
  目標        … サブタブ: ランニング / 体重・脂質
  習慣        … 日次ログ・瞑想・ストレッチの30日詳細

ナビは st.radio + session_state なので、5分自動リロード後も選択ページが維持される。
実行: streamlit run app.py
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import notion_api as na

st.set_page_config(page_title="KPI Dashboard", page_icon="🎯", layout="wide")

try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=5 * 60 * 1000, key="auto_refresh")
except ImportError:
    pass

TOKEN = st.secrets["NOTION_TOKEN"]

JST = dt.timezone(dt.timedelta(hours=9))
SIGNAL_COLOR = {"青": "#3B82F6", "緑": "#22C55E", "黄": "#EAB308",
                "橙": "#F97316", "赤": "#EF4444", None: "#6B7280"}

GOAL_KM = 100.0
STRETCH_KM = 150.0  # 年間ストレッチ目標
WEEKLY_EN_MIN = 420.0  # 英語 週目標(7時間)
URL_LEARNING_DB = "https://app.notion.com/p/0f1cb96b58694555b6315adf5b711ea4"  # 学習記録DB
URL_TADOKU_DB = "https://app.notion.com/p/26f6e5b9ef1080e79a20d92691202f71"    # 多読記録
GOAL_START = dt.date(2026, 1, 1)   # 計画ライン・対計画計算の起点(年初から)
GOAL_END = dt.date(2026, 12, 31)
WAIST_GOAL = 76.0  # 腹囲目標 cm

TASK_OPEN = {"未着手", "進行中", "中断"}

# クリックで飛ぶNotionページ
URL_TASK_PAGE = "https://app.notion.com/p/1c36e5b9ef1080df9268f55870ef3ae4"   # ✅ タスク管理
URL_THOUGHT_DB = "https://app.notion.com/p/3456e5b9ef1080e3aeaaed83ca783463"  # 思考記録DB
URL_RUN_DB = "https://app.notion.com/p/b3f7d01fea0349b58bedd56d7df58fb4"      # ランニング記録DB
URL_DAILY_DB = "https://app.notion.com/p/44f2b9e428fe48749d4b4bb5ce66e51f"    # 日次ログDB
URL_MED_DB = "https://app.notion.com/p/1f06e5b9ef1080868d9be79a2fe00ab6"      # 瞑想記録DB
URL_COND_DB = "https://app.notion.com/p/0c718e4a65d64b95bcc192ecb9106b70"     # コンディション記録DB


def linked_header(title: str, url: str) -> str:
    return (f"<h4 style='margin-bottom:0.4rem'><a href='{url}' target='_blank' "
            f"style='text-decoration:none;color:inherit'>{title} "
            f"<span style='font-size:0.7em;color:#6B7280'>↗</span></a></h4>")

MODE_RULES = {
    "絶好調": ("🔵", "攻めてよい日。負荷の高いタスクや挑戦を前に"),
    "通常運転": ("🟢", "通常タスク+ラン可(種別自由)"),
    "セーブ運転(身体)": ("🟡", "重要タスク1件に絞る/ランはEasy・Recoveryのみ/22時半就寝"),
    "セーブ運転(神経)": ("🟡", "会議・対人負荷を減らす/瞑想を優先/カフェイン午前まで"),
    "要注意": ("🟠", "無理をしない。Mustのみ/軽い運動に留める/早めの休息"),
    "回復日": ("🔴", "ランなし/最低限のMustのみ/夜は入浴+早寝"),
    "未記録": ("⚪", "今朝のコンディションを記録すると今日のモードが出ます"),
}


def today_mode(total, body, ans) -> str:
    if total == "赤":
        return "回復日"
    if total == "橙":
        return "要注意"
    if total == "青":
        return "絶好調"
    if total == "緑":
        return "通常運転"
    if total == "黄":
        body_bad = body in ("黄", "橙", "赤")
        ans_bad = ans in ("黄", "橙", "赤")
        if body_bad and not ans_bad:
            return "セーブ運転(身体)"
        if ans_bad and not body_bad:
            return "セーブ運転(神経)"
        return "セーブ運転(身体)"
    return "未記録"


@st.cache_data(ttl=300, show_spinner="Notionから取得中…")
def load_data(days: int = 35) -> dict:
    d = na.fetch_all(TOKEN, days=days)
    d["_synced_at"] = dt.datetime.now(JST)
    return d


@st.cache_data(ttl=3600, show_spinner=False)
def load_alltime() -> dict:
    """通算・最長・成長ログ用の全期間データ(1時間キャッシュ)。"""
    d = na.fetch_alltime(TOKEN)
    d["_synced_at"] = dt.datetime.now(JST)
    return d


def longest_streak(dates: set) -> int:
    if not dates:
        return 0
    best, cur = 1, 1
    seq = sorted(dates)
    for a, b in zip(seq, seq[1:]):
        cur = cur + 1 if (b - a).days == 1 else 1
        best = max(best, cur)
    return best


def parse_pace_sec(text: str):
    """'6'30\"' / '6:30' / '6分30秒' などを 秒/km に変換。失敗時 None。"""
    import re
    m = re.search(r"(\d+)[':分](\d{1,2})", text or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def fmt_pace(sec: float) -> str:
    return f"{int(sec // 60)}'{int(sec % 60):02d}\""


def measured_minutes(page) -> float | None:
    """「計測」date(開始〜終了)から分数を算出。未計測/計測中は None。"""
    start, end = na.prop_date_range(page, "計測")
    if not (start and end):
        return None
    try:
        t0 = pd.to_datetime(start, utc=True)
        t1 = pd.to_datetime(end, utc=True)
        mins = (t1 - t0).total_seconds() / 60
        return round(mins, 1) if 0 < mins < 24 * 60 else None  # 異常値ガード
    except Exception:
        return None


def parse_wake_min(text):
    """ISO日時('...T06:45:00+09:00') や '6:45' から 深夜0時基準の分。失敗時 None。"""
    import re
    if not isinstance(text, str) or not text:
        return None  # None/NaN/数値など文字列以外はすべて未記録扱い
    # ISO日時形式ならTの後ろの時刻を、そうでなければ全体から HH:MM を拾う
    m = re.search(r"T(\d{2}):(\d{2})", text) or re.search(r"(\d{1,2})[:時](\d{1,2})", text)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi if 0 <= h < 24 and 0 <= mi < 60 else None


def to_jst_date(series: pd.Series) -> pd.Series:
    ts = pd.to_datetime(series, format="mixed", utc=True)
    return ts.dt.tz_convert("Asia/Tokyo").dt.date


def streak_from(dates: set, today: dt.date) -> int:
    n, d = 0, today
    if d not in dates:
        d -= dt.timedelta(days=1)
    while d in dates:
        n += 1
        d -= dt.timedelta(days=1)
    return n


def comeback_stats(dates: set, today: dt.date) -> dict:
    """習慣の復帰力: 途切れ(ブランク)の回数と平均日数。"""
    if len(dates) < 2:
        return {"n": 0, "avg": None, "max": 0}
    seq = sorted(dates)
    gaps = [(b - a).days - 1 for a, b in zip(seq, seq[1:]) if (b - a).days > 1]
    return {
        "n": len(gaps),
        "avg": sum(gaps) / len(gaps) if gaps else None,
        "max": max(gaps) if gaps else 0,
    }


def signal_badge(label: str, value) -> str:
    color = SIGNAL_COLOR.get(value, SIGNAL_COLOR[None])
    return (
        f"<div style='text-align:center;padding:0.6rem;border-radius:12px;"
        f"background:{color}22;border:2px solid {color};'>"
        f"<div style='font-size:0.75rem;color:#9CA3AF'>{label}</div>"
        f"<div style='font-size:1.6rem;font-weight:700;color:{color}'>{value or '—'}</div></div>"
    )


def heat_row(label: str, days: list[dt.date], hits: set, color="#22C55E") -> str:
    cells = "".join(
        f"<span title='{d}' style='display:inline-block;width:16px;height:16px;"
        f"margin:1.5px;border-radius:3px;background:"
        f"{color if d in hits else '#374151'}'></span>"
        for d in days
    )
    return (f"<div style='margin-bottom:0.4rem'><span style='display:inline-block;"
            f"width:7em;color:#9CA3AF'>{label}</span>{cells}</div>")


def line_fig(df: pd.DataFrame, cols: dict[str, str], height=240) -> go.Figure:
    """cols = {列名: 色}"""
    fig = go.Figure()
    for col, color in cols.items():
        fig.add_trace(go.Scatter(x=df["date"], y=df[col], mode="lines+markers",
                                 name=col, line=dict(color=color, width=2),
                                 connectgaps=True))
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=10, b=10),
                      legend=dict(orientation="h", y=1.15))
    return fig


# ==== データ取得・整形 ====
data = load_data()
today = dt.datetime.now(JST).date()  # サーバーはUTCのため必ずJSTで日付判定

_errs = list(data.get("_errors", [])) + list(load_alltime().get("_errors", []))
if _errs:
    st.warning("一部のDBを取得できませんでした: " + " / ".join(_errs) +
               " — Notionで該当DBの「…」→「接続」にインテグレーションを追加してください")

cond = pd.DataFrame([
    {
        "date": na.prop_date(p, "日付"),
        "総合": na.prop_select(p, "信号判定"),
        "身体疲労": na.prop_select(p, "身体疲労軸信号"),
        "自律神経": na.prop_select(p, "自律神経軸信号"),
        "体調": na.prop_select(p, "体調ステータス"),
        "体重": na.prop_number(p, "体重_kg"),
        "体脂肪率": na.prop_number(p, "体脂肪率"),
        "骨格筋量": na.prop_number(p, "骨格筋量_kg"),
        "腹囲": na.prop_number(p, "腹囲_cm"),
        "睡眠スコア": na.prop_number(p, "睡眠スコア"),
        "睡眠時間": na.prop_number(p, "睡眠時間_分"),
        "BB起床": na.prop_number(p, "Body Battery起床"),
        "Oura": na.prop_number(p, "Ouraコンディション"),
        "HRV": na.prop_number(p, "夜間HRV_ms"),
        "RHR": na.prop_number(p, "RHR_bpm"),
        "ストレス": na.prop_number(p, "前日平均ストレス"),
        "体表温偏差": na.prop_number(p, "体表温偏差_℃"),
        "疲労度": na.prop_number(p, "主観的疲労度"),
        "幸福度": na.prop_number(p, "主観的幸福度"),
        "瞑想": na.prop_has_relation(p, "瞑想記録"),
        "起床": na.prop_date(p, "起床時間"),  # H:mm形式のdate型
        "フィードバック": na.prop_rich_text(p, "フィードバック"),
    }
    for p in data["condition"]
])
if not cond.empty:
    cond["date"] = to_jst_date(cond["date"])
    cond = cond.sort_values("date")

runs = pd.DataFrame([
    {
        "date": na.prop_date(p, "日時"),
        "km": na.prop_number(p, "距離_km") or 0.0,
        "ペース": na.prop_rich_text(p, "平均ペース"),
        "種別": na.prop_select(p, "種別"),
        "心拍": na.prop_number(p, "平均心拍_bpm"),
    }
    for p in data["running"]
])
if not runs.empty:
    runs["date"] = to_jst_date(runs["date"])
    runs = runs.sort_values("date")

logs = pd.DataFrame([
    {"date": na.prop_date(p, "日付"), "stretch": na.prop_checkbox(p, "ストレッチ")}
    for p in data["daily_log"] if na.prop_date(p, "日付")
])
if not logs.empty:
    logs["date"] = to_jst_date(logs["date"])

meals = pd.DataFrame([
    {
        "date": na.prop_date(p, "日付"),
        "脂質": na.prop_number(p, "脂質_g"),
        "繊維": na.prop_number(p, "食物繊維_g"),
        "塩分": na.prop_number(p, "食塩相当量_g"),
        "スコア": na.prop_number(p, "健康スコア"),
        "飲酒": na.prop_checkbox(p, "飲酒"),
        "夕食帯": na.prop_select(p, "夕食時間帯"),
    }
    for p in data.get("meals", []) if na.prop_date(p, "日付")
])
if not meals.empty:
    meals["date"] = to_jst_date(meals["date"])
    meals = meals.sort_values("date")
drink_dates = set(meals[meals["飲酒"]]["date"]) if not meals.empty else set()

log_dates = set(logs["date"]) if not logs.empty else set()
stretch_dates = set(logs[logs["stretch"]]["date"]) if not logs.empty else set()
# 瞑想実施日は後段の瞑想DB(_all_med_dates)を正とする。ここでは暫定。
med_dates = set(cond[cond["瞑想"]]["date"]) if not cond.empty else set()

# ==== 全期間データ(通算・最長・成長ログ) ====
def _to_date(s: str) -> dt.date:
    return pd.to_datetime(s, utc=True).tz_convert("Asia/Tokyo").date()


alltime = load_alltime()
_all_log_dates, growth_entries = set(), []
for p in alltime["daily_all"]:
    d = na.prop_date(p, "日付")
    if not d:
        continue
    d = _to_date(d)
    _all_log_dates.add(d)
    for key in ("今日成長したこと1", "今日成長したこと2", "今日成長したこと3"):
        text = na.prop_rich_text(p, key).strip()
        if text:
            growth_entries.append((d, text))

_all_med_dates, med_total_min = set(), 0.0
med_min_by_day: dict = {}
for p in alltime.get("meditation_all", []):
    d = na.prop_date(p, "日付")
    if d:
        d = _to_date(d)
        _all_med_dates.add(d)
        mins = na.prop_number(p, "時間") or 0.0
        med_total_min += mins
        med_min_by_day[d] = med_min_by_day.get(d, 0.0) + mins
if not _all_med_dates:  # 瞑想DB未接続時のフォールバック
    for p in alltime["condition_all"]:
        d = na.prop_date(p, "日付")
        if d and na.prop_has_relation(p, "瞑想記録"):
            _all_med_dates.add(_to_date(d))

lifetime_km = sum(na.prop_number(p, "距離_km") or 0.0 for p in alltime["running_all"])

# 反証体験ログ(再掲示プール用)
hansho_entries = []
for p in alltime.get("hansho_all", []):
    d = na.prop_date(p, "日付")
    event = na.prop_title(p, "出来事")
    learning = na.prop_rich_text(p, "学び・再解釈").strip()
    if d and event and learning:
        hansho_entries.append({
            "date": _to_date(d),
            "event": event,
            "learning": learning,
            "category": na.prop_select(p, "カテゴリ") or "",
            "concepts": na.prop_multi_select(p, "対応概念"),
        })

# 日別タスク消化率(実行日時ベース、過去30日)
_task_daily: dict = {}
for p in alltime.get("tasks_30d", []):
    exec_date = na.prop_date(p, "実行日時")
    if not exec_date:
        continue
    d = _to_date(exec_date)
    tot, done = _task_daily.get(d, (0, 0))
    is_done = na.prop_status(p, "ステータス") not in TASK_OPEN
    _task_daily[d] = (tot + 1, done + (1 if is_done else 0))
task_rate_by_day = {d: done / tot for d, (tot, done) in _task_daily.items() if tot > 0}

# 成長タブ用: 全期間のコンディション/ラン
cond_all = pd.DataFrame([
    {
        "date": na.prop_date(p, "日付"),
        "RHR": na.prop_number(p, "RHR_bpm"),
        "HRV": na.prop_number(p, "夜間HRV_ms"),
        "睡眠スコア": na.prop_number(p, "睡眠スコア"),
        "体重": na.prop_number(p, "体重_kg"),
        "体脂肪率": na.prop_number(p, "体脂肪率"),
        "骨格筋量": na.prop_number(p, "骨格筋量_kg"),
    }
    for p in alltime.get("condition_all", []) if na.prop_date(p, "日付")
])
if not cond_all.empty:
    cond_all["date"] = cond_all["date"].map(_to_date)
    cond_all = cond_all.sort_values("date")

runs_all = pd.DataFrame([
    {"date": na.prop_date(p, "日時"),
     "km": na.prop_number(p, "距離_km") or 0.0,
     "pace_sec": None}
    for p in alltime.get("running_all", []) if na.prop_date(p, "日時")
])
if not runs_all.empty:
    runs_all["date"] = runs_all["date"].map(_to_date)
    runs_all["pace_sec"] = [parse_pace_sec(na.prop_rich_text(p, "平均ペース"))
                            for p in alltime.get("running_all", [])
                            if na.prop_date(p, "日時")]
    runs_all = runs_all.sort_values("date")

at_df = pd.DataFrame([
    {
        "date": na.prop_date(p, "発生日時"),
        "強度": na.prop_number(p, "感情強度(0~5)"),
        "バイアス": na.prop_multi_select(p, "バイアス(悪癖)"),
        "内容": na.prop_title(p, "自動思考の内容"),
        "再解釈": na.prop_rich_text(p, "振り返り・再解釈").strip(),
    }
    for p in alltime.get("autothought_all", []) if na.prop_date(p, "発生日時")
])
if not at_df.empty:
    at_df["date"] = at_df["date"].map(_to_date)
    at_df = at_df.sort_values("date")
task_done_by_day = {d: done for d, (tot, done) in _task_daily.items() if tot > 0}

# 今週 vs 先週同時点のタスク比較(モチベーション指標)
_wk_start = today - dt.timedelta(days=today.weekday())
_lw_start = _wk_start - dt.timedelta(days=7)
_lw_until = _lw_start + (today - _wk_start)  # 先週の「同じ曜日」まで


def _week_task_sum(start, until):
    done = sum(d2 for d, (t2, d2) in _task_daily.items() if start <= d <= until)
    tot = sum(t2 for d, (t2, d2) in _task_daily.items() if start <= d <= until)
    return tot, done


TW_TOT, TW_DONE = _week_task_sum(_wk_start, today)
LW_TOT_SAME, LW_DONE_SAME = _week_task_sum(_lw_start, _lw_until)
LW_TOT_FULL, LW_DONE_FULL = _week_task_sum(_lw_start, _lw_start + dt.timedelta(days=6))

# 瞑想実施の判定は瞑想記録DBの日付を正とする(コンディションのリレーション非依存)
# 当日〜直近は5分キャッシュ側(meditation_recent)で鮮度を確保し、全期間と合算する
_recent_med_dates, _recent_med_min = set(), {}
for p in data.get("meditation_recent", []):
    d = na.prop_date(p, "日付")
    if d:
        d = _to_date(d)
        _recent_med_dates.add(d)
        _recent_med_min[d] = _recent_med_min.get(d, 0.0) + (na.prop_number(p, "時間") or 0.0)
if _all_med_dates or _recent_med_dates:
    med_dates = _all_med_dates | _recent_med_dates
    med_min_by_day.update(_recent_med_min)  # 直近分は新しい値で上書き

TOTALS = {
    "log": (len(_all_log_dates), longest_streak(_all_log_dates)),
    "med": (len(_all_med_dates), longest_streak(_all_med_dates)),
}


# ==== ナビ(自動リロードしても選択ページを維持) ====
head_l, head_r = st.columns([1.2, 2])
_sync = data.get("_synced_at")
_sync_txt = f" ・ 同期 {_sync.strftime('%H:%M')}" if _sync else ""
head_l.markdown(
    f"## 🎯 KPI <span style='font-size:0.9rem;color:#9CA3AF'>{today}{_sync_txt}</span>",
    unsafe_allow_html=True,
)
page = head_r.radio("page", ["羅針盤", "今日", "コンディション", "目標", "習慣", "成長"],
                    horizontal=True, key="nav", label_visibility="collapsed")


# ================= 今日(壁掛けビュー) =================
def render_today():
    latest = cond.iloc[-1] if not cond.empty else None
    is_today = latest is not None and latest["date"] == today
    sig = (latest["総合"], latest["身体疲労"], latest["自律神経"]) if is_today else (None, None, None)
    mode = today_mode(*sig)
    icon, action = MODE_RULES[mode]



    c1, c2, c3, c4 = st.columns([1, 1, 1, 2.2])
    c1.markdown(signal_badge("総合", sig[0]), unsafe_allow_html=True)
    c2.markdown(signal_badge("身体疲労軸", sig[1]), unsafe_allow_html=True)
    c3.markdown(signal_badge("自律神経軸", sig[2]), unsafe_allow_html=True)
    c4.markdown(
        f"<div style='padding:0.6rem 1rem;border-radius:12px;background:#161B22;"
        f"border:1px solid #30363D;height:100%'>"
        f"<div style='font-size:1.3rem;font-weight:700'>{icon} 今日のモード: {mode}</div>"
        f"<div style='color:#9CA3AF;margin-top:0.3rem'>{action}</div></div>",
        unsafe_allow_html=True,
    )
    if is_today and latest["フィードバック"]:
        st.caption(f"💬 {latest['フィードバック']}")

    # ---- 今日の免責(悪い日にだけ現れる) ----
    now_hour = dt.datetime.now(JST).hour
    tasks_ = data["tasks_today"]
    n_total_ = len(tasks_)
    n_open_ = sum(1 for t in tasks_ if na.prop_status(t, "ステータス") in TASK_OPEN)
    low_rate_evening = (now_hour >= 18 and n_total_ > 0
                        and (n_total_ - n_open_) / n_total_ < 0.5)
    bad_day = mode in ("回復日", "要注意")

    if bad_day or low_rate_evening:
        RECOVERY_MSGS = [
            "今日は消化率よりも、回復が仕事。",
            "嵐は過ぎるのを待てばいい。 —— 2024年の自分より",
            "芯は安定し、状況をうまく乗りこなす。休むのも乗りこなし方のひとつ(Like Bamboo)",
            "ボロボロより、元気でいるほうがずっといい。 —— 早紀の言葉より",
        ]
        TASK_MSGS = [
            "未実施は失敗ではなく、「やらない」という判断。",
            "努力とは、恐れから逃げる自己追い込みではなく、価値に向かう一歩(9つの概念より)",
            "完璧な日である必要はない。明日も棒が並べば、それが成果。",
            "成功とは、価値観に沿って動いている時間が増えていくこと。今日の数字ではない。",
        ]
        import random
        pool = RECOVERY_MSGS if bad_day else TASK_MSGS
        msg = random.Random(today.toordinal() + 11).choice(pool)
        st.markdown(
            f"<div style='padding:0.9rem 1.2rem;border-radius:12px;"
            f"background:#3B82F615;border:1px solid #3B82F6;margin-top:0.4rem'>"
            f"<div style='color:#3B82F6;font-size:0.72rem;font-weight:700'>"
            f"🕊️ 今日の免責</div>"
            f"<div style='font-size:1.05rem;margin-top:0.25rem'>{msg}</div></div>",
            unsafe_allow_html=True)

    # ---- 今日のチェックボード ----
    learn_dates = set()
    for p in data.get("learning", []):
        d = na.prop_date(p, "日付")
        if d:
            learn_dates.add(pd.to_datetime(d, utc=True).tz_convert("Asia/Tokyo").date())
    run_today = (not runs.empty) and (today in set(runs["date"]))

    # (絵文字, 名前, 済んだか, リンク先, ストリーク, コア習慣か)
    tiles = [
        ("💆", "コンディション", is_today, URL_COND_DB, None, True),
        ("📝", "日次ログ", today in log_dates, URL_DAILY_DB,
         streak_from(log_dates, today), True),
        ("🧘", "瞑想", today in med_dates, URL_MED_DB,
         streak_from(med_dates, today), True),
        ("🤸", "ストレッチ", today in stretch_dates, URL_DAILY_DB,
         streak_from(stretch_dates, today), True),
        ("🎧", "学習", today in learn_dates, URL_LEARNING_DB, None, False),
        ("🏃", "ラン", run_today, URL_RUN_DB, None, False),
    ]
    core_done = sum(1 for _, _, done, *_ in tiles if done and _)
    core_total = sum(1 for t in tiles if t[5])
    core_done = sum(1 for t in tiles if t[5] and t[2])

    cols = st.columns(len(tiles))
    for c, (emoji, name, done, url, streak, core) in zip(cols, tiles):
        if done:
            html = (
                f"<a href='{url}' target='_blank' style='text-decoration:none'>"
                f"<div style='text-align:center;padding:0.65rem 0.2rem;border-radius:14px;"
                f"background:#22C55E22;border:2px solid #22C55E'>"
                f"<div style='font-size:1.6rem'>{emoji}</div>"
                f"<div style='font-size:0.78rem;font-weight:700;color:#22C55E'>"
                f"{name} ✓</div></div></a>")
        elif core:
            if streak:
                nudge = f"🔥{streak}→{streak + 1}"
            else:
                cb = comeback_stats(
                    log_dates if name == "日次ログ" else
                    med_dates if name == "瞑想" else stretch_dates, today)
                nudge = (f"平均{cb['avg']:.1f}日で復帰してきた"
                         if cb["avg"] else "今日から")
            html = (
                f"<a href='{url}' target='_blank' style='text-decoration:none'>"
                f"<div style='text-align:center;padding:0.65rem 0.2rem;border-radius:14px;"
                f"background:#161B22;border:2px dashed #6B7280'>"
                f"<div style='font-size:1.6rem;filter:grayscale(1);opacity:0.55'>{emoji}</div>"
                f"<div style='font-size:0.78rem;color:#9CA3AF'>{name}</div>"
                f"<div style='font-size:0.7rem;color:#EAB308'>{nudge}</div></div></a>")
        else:
            html = (
                f"<a href='{url}' target='_blank' style='text-decoration:none'>"
                f"<div style='text-align:center;padding:0.65rem 0.2rem;border-radius:14px;"
                f"background:#161B22;border:1px solid #30363D'>"
                f"<div style='font-size:1.6rem;filter:grayscale(1);opacity:0.4'>{emoji}</div>"
                f"<div style='font-size:0.78rem;color:#6B7280'>{name}</div></div></a>")
        c.markdown(html, unsafe_allow_html=True)

    if core_done == core_total:
        st.caption(f"🎉 今日のコア習慣 {core_total}/{core_total} 完了。あとは自由時間")
    else:
        st.caption(f"コア習慣 {core_done}/{core_total} ・ タイルをタップで記録へ"
                   "(🎧🏃は任意)")

    st.divider()
    mid_l, mid_r = st.columns([2, 1])

    with mid_l:
        st.markdown(linked_header("🏃 100kmチャレンジ (2026年累計)", URL_RUN_DB),
                    unsafe_allow_html=True)
        total_km = float(runs["km"].sum()) if not runs.empty else 0.0
        elapsed = (today - GOAL_START).days + 1
        period = (GOAL_END - GOAL_START).days + 1
        on_pace_km = GOAL_KM * max(elapsed, 0) / period
        need_per_week = (GOAL_KM - total_km) / max((GOAL_END - today).days, 1) * 7
        m1, m2, m3 = st.columns(3)
        m1.metric("累計", f"{total_km:.1f} km", f"{total_km - on_pace_km:+.1f} km 対計画")
        m2.metric("達成率", f"{total_km / GOAL_KM * 100:.0f}%")
        m3.metric("必要ペース", f"{max(need_per_week, 0):.1f} km/週")
        st.progress(min(total_km / GOAL_KM, 1.0))

    with mid_r:
        st.markdown("#### ⚖️ 体重トレンド")
        w = cond.dropna(subset=["体重"]) if not cond.empty else pd.DataFrame()
        recent = w[w["date"] > today - dt.timedelta(days=7)]["体重"] if not w.empty else pd.Series(dtype=float)
        prev = w[(w["date"] <= today - dt.timedelta(days=7)) &
                 (w["date"] > today - dt.timedelta(days=14))]["体重"] if not w.empty else pd.Series(dtype=float)
        if len(recent) >= 2 and len(prev) >= 2:
            delta = recent.mean() - prev.mean()
            if delta < -0.05:
                arrow, color, note = "↘", "#22C55E", "減少ペース"
            elif delta > 0.05:
                arrow, color, note = "↗", "#EF4444", "増加ペース"
            else:
                arrow, color, note = "→", "#9CA3AF", "横ばい"
            st.markdown(
                f"<div style='text-align:center;padding:0.9rem;border-radius:12px;"
                f"background:{color}15;border:2px solid {color}'>"
                f"<div style='font-size:2.4rem;font-weight:800;color:{color}'>{arrow}</div>"
                f"<div style='font-size:1.1rem;font-weight:600'>{delta:+.2f} kg/週</div>"
                f"<div style='color:#9CA3AF;font-size:0.75rem'>{note}(7日移動平均)</div></div>",
                unsafe_allow_html=True,
            )
        else:
            st.info("直近14日で4回以上の測定が必要です")

    st.divider()
    b1, b2, b3 = st.columns([1.4, 1, 1])

    with b1:
        st.markdown("#### ✅ 習慣ストリーク")
        h1, h2 = st.columns(2)
        h1.metric("📝 日次ログ", f"{streak_from(log_dates, today)} 日連続")
        h1.caption(f"通算 {TOTALS['log'][0]} 日・最長 {TOTALS['log'][1]} 日 [↗]({URL_DAILY_DB})")
        h2.metric("🧘 瞑想", f"{streak_from(med_dates, today)} 日連続")
        h2.caption(f"通算 {TOTALS['med'][0]} 日・最長 {TOTALS['med'][1]} 日・"
                   f"累計 {med_total_min / 60:.1f} 時間 [↗]({URL_MED_DB})")
        last14 = sorted(today - dt.timedelta(days=i) for i in range(14))
        st.markdown(heat_row("日次ログ", last14, log_dates), unsafe_allow_html=True)

    with b2:
        st.markdown(linked_header("📋 今日のタスク", URL_TASK_PAGE), unsafe_allow_html=True)
        tasks = data["tasks_today"]
        n_total = len(tasks)
        n_open = sum(1 for t in tasks if na.prop_status(t, "ステータス") in TASK_OPEN)
        must_due = [t for t in data["tasks_must_due"]
                    if na.prop_status(t, "ステータス") in TASK_OPEN]
        if n_total:
            pomo_today = sum(na.prop_number(t, "ポモ数") or 0 for t in tasks)
            m_a, m_b = st.columns(2)
            m_a.metric("残り", f"{n_open} / {n_total}")
            m_b.metric("🍅 ポモ", f"{pomo_today:.0f}")
            st.progress((n_total - n_open) / n_total)
            diff = TW_DONE - LW_DONE_SAME
            race = ("先週の今頃と同じペース" if diff == 0 else
                    f"先週の今頃より {diff:+d} 件")
            st.caption(f"今週の完了 ✓{TW_DONE} 件 — {race}")
        else:
            st.info("今日のタスクは未生成です")
        if must_due:
            st.markdown(f"🔴 **今週期限のMust: {len(must_due)}件**")
            top = must_due[0]
            st.markdown(
                f"<span style='color:#9CA3AF;font-size:0.85rem'>最優先: "
                f"<a href='{top.get('url', URL_TASK_PAGE)}' target='_blank'>"
                f"{na.prop_title(top, '名前')}</a></span>",
                unsafe_allow_html=True,
            )

    with b3:
        st.markdown(linked_header("💭 思考在庫", URL_THOUGHT_DB), unsafe_allow_html=True)
        n_thoughts = len(data["thoughts_open"])
        n_new_wk = len(data.get("thoughts_new_week", []))
        n_done_wk = len(data.get("thoughts_done_week", []))
        st.metric("在庫(完了以外)", f"{n_thoughts} 件",
                  f"今週 +{n_new_wk} / ✓{n_done_wk}", delta_color="off")

        month = data.get("thoughts_month", [])
        n_month = len(month)
        n_done_m = sum(1 for t in month
                       if na.prop_select(t, "ステータス") == "完了")
        if n_month:
            st.caption(f"今月: {n_month} 件起票 / {n_done_m} 件完了")

        # アクション・問い(完了 = アーカイブ or タスク化 or 回答済み)
        acts = data.get("actions_all", [])
        def _act_done(p):
            return (na.prop_checkbox(p, "アーカイブ")
                    or na.prop_has_relation(p, "タスク化先")
                    or bool(na.prop_rich_text(p, "回答").strip()))
        open_acts = [p for p in acts if not _act_done(p)]
        n_act_done = len(acts) - len(open_acts)
        st.metric("アクション・問い 残", f"{len(open_acts)} 件",
                  f"完了 {n_act_done} 件", delta_color="off")

        # エイジング: 残アイテムの滞留日数(createdTimeから算出)
        ages = sorted(
            (today - _to_date(p["created_time"])).days
            for p in open_acts if p.get("created_time"))
        th_ages = sorted(
            (today - _to_date(p["created_time"])).days
            for p in data["thoughts_open"] if p.get("created_time"))
        if ages or th_ages:
            def _med(xs): return xs[len(xs) // 2] if xs else 0
            parts = []
            if ages:
                parts.append(f"ア・問: 最古{ages[-1]}日 / 中央{_med(ages)}日")
            if th_ages:
                parts.append(f"思考: 最古{th_ages[-1]}日 / 中央{_med(th_ages)}日")
            stale = sum(1 for a in ages if a >= 30) + sum(1 for a in th_ages if a >= 30)
            st.caption("⏳ 滞留 — " + " ・ ".join(parts))
            if stale:
                st.caption(f"🕰️ 30日以上動いていないもの {stale} 件 → 週次レビューで棚卸しを")

    st.divider()
    st.markdown("#### 🌱 あの日の自分から")
    import random
    pool = [("growth", d, t) for d, t in growth_entries
            if d >= today - dt.timedelta(days=90)]
    pool += [("hansho", h["date"], h) for h in hansho_entries]  # 反証は全期間
    if pool:
        kind, d, item = random.Random(today.toordinal()).choice(pool)
        days_ago = (today - d).days
        if kind == "growth":
            st.markdown(
                f"<div style='padding:0.8rem 1rem;border-radius:12px;background:#161B22;"
                f"border-left:4px solid #22C55E'>"
                f"<div style='color:#9CA3AF;font-size:0.75rem'>"
                f"🌱 成長 — {d} ({days_ago}日前)</div>"
                f"<div style='font-size:1.05rem;margin-top:0.2rem'>{item}</div></div>",
                unsafe_allow_html=True,
            )
        else:
            cat = f"「{item['category']}」" if item["category"] else ""
            learning = item["learning"]
            if len(learning) > 150:
                learning = learning[:150] + "…"
            st.markdown(
                f"<div style='padding:0.8rem 1rem;border-radius:12px;background:#161B22;"
                f"border-left:4px solid #A78BFA'>"
                f"<div style='color:#9CA3AF;font-size:0.75rem'>"
                f"🛡️ 反証体験{cat} — {d} ({days_ago}日前)</div>"
                f"<div style='font-weight:600;margin-top:0.2rem'>{item['event']}</div>"
                f"<div style='color:#D1D5DB;font-size:0.9rem;margin-top:0.2rem'>"
                f"{learning}</div></div>",
                unsafe_allow_html=True,
            )
    st.caption("詳しい成長指標は上部ナビの「成長」タブへ")


# ================= コンディション詳細 =================
def render_condition():
    if cond.empty:
        st.info("コンディション記録がありません")
        return

    # 信号履歴(直近14日)
    st.markdown("#### 🚥 信号履歴(14日)")
    last14 = sorted(today - dt.timedelta(days=i) for i in range(14))
    sig_map = {r["date"]: r["総合"] for _, r in cond.iterrows()}
    cells = "".join(
        f"<span title='{d}: {sig_map.get(d) or '未記録'}' style='display:inline-block;"
        f"width:34px;height:34px;margin:2px;border-radius:6px;text-align:center;"
        f"line-height:34px;font-size:0.65rem;color:#fff;background:"
        f"{SIGNAL_COLOR.get(sig_map.get(d), '#374151')}'>{d.day}</span>"
        for d in last14
    )
    st.markdown(f"<div>{cells}</div>", unsafe_allow_html=True)
    st.divider()

    # 睡眠サマリ
    st.markdown("#### 😴 睡眠")
    sl = cond.dropna(subset=["睡眠時間"])
    s1, s2, _sp = st.columns([1, 1, 2])
    def _fmt_min(m): return f"{int(m // 60)}時間{int(m % 60):02d}分"
    r7 = sl[sl["date"] > today - dt.timedelta(days=7)]["睡眠時間"]
    if len(r7):
        s1.metric("平均睡眠(7日)", _fmt_min(r7.mean()))
    if len(sl):
        s2.metric("平均睡眠(30日)", _fmt_min(sl["睡眠時間"].mean()))
    st.divider()

    # 最新値サマリ(30日平均との差をデルタ表示)
    st.markdown("#### 📊 最新バイタル(Δは30日平均との差)")
    latest = cond.iloc[-1]
    metrics = [
        ("睡眠スコア", "", 0), ("BB起床", "", 0), ("Oura", "", 0),
        ("HRV", " ms", 0), ("RHR", " bpm", 0), ("ストレス", "", 0),
        ("体表温偏差", " ℃", 2), ("疲労度", " /5", 0), ("幸福度", " /5", 0),
    ]
    cols = st.columns(len(metrics))
    for c, (name, unit, nd) in zip(cols, metrics):
        val = latest[name]
        avg = cond[name].mean()
        if pd.notna(val):
            c.metric(name, f"{val:.{nd}f}{unit}",
                     f"{val - avg:+.{max(nd,1)}f}" if pd.notna(avg) else None)
        else:
            c.metric(name, "—")
    st.divider()

    # 30日トレンド
    st.markdown("#### 📈 30日トレンド")
    g1, g2 = st.columns(2)
    with g1:
        st.caption("回復系: 睡眠スコア / Body Battery / Oura")
        st.plotly_chart(line_fig(cond, {"睡眠スコア": "#3B82F6", "BB起床": "#22C55E",
                                        "Oura": "#A78BFA"}), use_container_width=True)
        st.caption("自律神経系: 夜間HRV / 前日平均ストレス")
        st.plotly_chart(line_fig(cond, {"HRV": "#22C55E", "ストレス": "#EF4444"}),
                        use_container_width=True)
    with g2:
        st.caption("主観: 疲労度 / 幸福度 (1-5)")
        st.plotly_chart(line_fig(cond, {"疲労度": "#F97316", "幸福度": "#EAB308"}),
                        use_container_width=True)
        st.caption("発熱兆候: 体表温偏差 (℃)")
        fig = line_fig(cond, {"体表温偏差": "#EC4899"})
        fig.add_hline(y=0.5, line_dash="dash", line_color="#EF4444",
                      annotation_text="発熱傾向 +0.5℃")
        st.plotly_chart(fig, use_container_width=True)

    # ---- 飲酒・夕食時間帯と翌朝の回復 ----
    st.divider()
    st.markdown("#### 🍽️ 食事と翌朝の回復(30日)")
    # 翌朝指標: 記録日Dの値は前夜の結果 → 前日dに紐づける
    next_hrv = {r["date"] - dt.timedelta(days=1): r["HRV"]
                for _, r in cond.iterrows() if pd.notna(r["HRV"])}
    next_sleep = {r["date"] - dt.timedelta(days=1): r["睡眠スコア"]
                  for _, r in cond.iterrows() if pd.notna(r["睡眠スコア"])}
    next_bb = {r["date"] - dt.timedelta(days=1): r["BB起床"]
               for _, r in cond.iterrows() if pd.notna(r["BB起床"])}

    if not meals.empty:
        c_alc, c_din = st.columns(2)
        with c_alc:
            st.caption("🍺 飲酒と翌朝")
            alc_days = [d for d in meals["date"] if d in drink_dates]
            sober_days = [d for d in meals["date"] if d not in drink_dates]
            rows = []
            for label, metric_map in [("HRV", next_hrv), ("睡眠スコア", next_sleep)]:
                a = [metric_map[d] for d in alc_days if d in metric_map]
                s = [metric_map[d] for d in sober_days if d in metric_map]
                if len(a) >= 2 and len(s) >= 3:
                    rows.append((label, sum(a) / len(a), sum(s) / len(s), len(a)))
            if rows:
                for label, a_avg, s_avg, n in rows:
                    st.metric(f"翌朝{label}: 飲酒日 (n={n})", f"{a_avg:.0f}",
                              f"{a_avg - s_avg:+.1f} vs 非飲酒日", delta_color="off")
            else:
                st.info("飲酒日のデータが貯まると比較が出ます")
        with c_din:
            st.caption("🕰️ 夕食時間帯と翌朝")
            rows = []
            for band in ["早い(〜19時)", "標準(19-21時)", "遅い(21時〜)"]:
                days = list(meals[meals["夕食帯"] == band]["date"])
                sl = [next_sleep[d] for d in days if d in next_sleep]
                bb = [next_bb[d] for d in days if d in next_bb]
                if sl or bb:
                    rows.append((band,
                                 sum(sl) / len(sl) if sl else None,
                                 sum(bb) / len(bb) if bb else None,
                                 max(len(sl), len(bb))))
            if rows:
                df = pd.DataFrame(
                    [(b, f"{s:.0f}" if s else "—", f"{v:.0f}" if v else "—", n)
                     for b, s, v, n in rows],
                    columns=["夕食時間帯", "翌朝睡眠スコア", "翌朝BB", "n"])
                st.dataframe(df, hide_index=True, use_container_width=True)
            else:
                st.info("夕食時間帯の記録が貯まると比較が出ます")
        st.caption("※ 相関の観察。会食日は飲酒・遅い夕食・外食が重なりやすい点に注意")

    # ---- 運動・睡眠・起床とタスク処理 ----
    st.divider()
    st.markdown("#### 🏃💤 運動・睡眠とタスク処理(30日)")
    st.caption("タスク処理は 消化率(質) と 完了数(量) の2軸で評価")

    def _avg(xs):
        return sum(xs) / len(xs) if xs else None

    def _task_pair(days: list) -> tuple:
        rates = [task_rate_by_day[d] * 100 for d in days if d in task_rate_by_day]
        dones = [task_done_by_day[d] for d in days if d in task_done_by_day]
        return _avg(rates), _avg(dones), len(rates)

    p1, p2 = st.columns(2)
    with p1:
        st.caption("🏃 ランニング実施日(当日)")
        run30 = {d for d in (runs["date"] if not runs.empty else [])
                 if d >= today - dt.timedelta(days=30)}
        task_days = sorted(task_rate_by_day.keys())
        r_rate, r_done, r_n = _task_pair([d for d in task_days if d in run30])
        n_rate, n_done, n_n = _task_pair([d for d in task_days if d not in run30])
        if r_n >= 2 and n_n >= 3:
            st.metric(f"ラン日の消化率 (n={r_n})", f"{r_rate:.0f}%",
                      f"{r_rate - n_rate:+.0f}pt vs 非ラン日", delta_color="off")
            st.metric("ラン日の完了数/日", f"{r_done:.1f} 件",
                      f"{r_done - n_done:+.1f} vs 非ラン日", delta_color="off")
        else:
            st.info("ラン実施日のデータ蓄積待ちです")

    with p2:
        st.caption("💤 前夜の睡眠時間別(その日のタスク)")
        sl = cond.dropna(subset=["睡眠時間"]) if not cond.empty else pd.DataFrame()
        if not sl.empty:
            rows = []
            for label, lo, hi in [("6h未満", 0, 360), ("6〜7h", 360, 420),
                                   ("7h以上", 420, 10_000)]:
                days = [r["date"] for _, r in sl.iterrows()
                        if lo <= r["睡眠時間"] < hi]
                rate, done, n = _task_pair(days)
                if n:
                    rows.append((label, f"{rate:.0f}%", f"{done:.1f}件", n))
            if rows:
                st.dataframe(pd.DataFrame(rows,
                             columns=["睡眠", "消化率", "完了数/日", "n"]),
                             hide_index=True, use_container_width=True)
        else:
            st.info("睡眠時間のデータ蓄積待ちです")

    # 起床時刻(プロパティと記録がある場合のみ)
    wake = pd.DataFrame()
    if not cond.empty and "起床" in cond.columns:
        w = cond.copy()
        w["wake_min"] = w["起床"].map(parse_wake_min)
        wake = w.dropna(subset=["wake_min"])
    if not wake.empty:
        st.caption("🌅 起床時刻別(その日のタスク)")
        rows = []
        for label, lo, hi in [("6時前", 0, 360), ("6〜7時", 360, 420),
                               ("7時以降", 420, 1440)]:
            days = [r["date"] for _, r in wake.iterrows()
                    if lo <= r["wake_min"] < hi]
            rate, done, n = _task_pair(days)
            if n:
                rows.append((label, f"{rate:.0f}%", f"{done:.1f}件", n))
        if rows:
            st.dataframe(pd.DataFrame(rows,
                         columns=["起床", "消化率", "完了数/日", "n"]),
                         hide_index=True, use_container_width=True)
    else:
        st.caption("🌅 起床時間の記録が貯まると、起床時刻別のタスク処理が表示されます")
    st.caption("※ いずれも相関の観察(忙しい日はタスクが多く消化率が下がる等、母数の影響に注意)")

    # ---- ポモドーロ(30日) ----
    st.divider()
    st.markdown("#### 🍅 ポモドーロ(30日)")
    pomo_rows = []
    for p in alltime.get("tasks_30d", []):
        n_pomo = na.prop_number(p, "ポモ数")
        if not n_pomo:
            continue
        d = na.prop_date(p, "実行日時")
        if not d:
            continue
        if _to_date(d) < today - dt.timedelta(days=30):
            continue  # 取得は90日だがこのパネルは30日表示
        pomo_rows.append({"date": _to_date(d), "pomo": n_pomo,
                          "min": na.prop_formula_number(p, "実績")})
    if pomo_rows:
        pdf_ = pd.DataFrame(pomo_rows)
        daily_pomo = pdf_.groupby("date")["pomo"].sum()
        total_pomo = int(pdf_["pomo"].sum())
        withmin = pdf_.dropna(subset=["min"])
        cyc = (withmin["min"].sum() / withmin["pomo"].sum()) if len(withmin) else None
        q1, q2, q3 = st.columns(3)
        q1.metric("30日合計", f"{total_pomo} 🍅")
        q2.metric("実施日の平均", f"{daily_pomo.mean():.1f} 🍅/日")
        if cyc:
            state = ("✅ 健全" if 25 <= cyc <= 35 else
                     "⚠️ 休憩スキップ気味" if cyc < 25 else "⚠️ ぶっ通し気味")
            q3.metric("平均サイクル長", f"{cyc:.0f} 分/🍅", state, delta_color="off")
        fig = go.Figure(go.Bar(x=daily_pomo.index, y=daily_pomo.values,
                               marker_color="#EF4444"))
        fig.update_layout(height=220, margin=dict(l=10, r=10, t=10, b=10),
                          yaxis_title="🍅/日")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("サイクル長=計測実績÷ポモ数。25〜35分なら「25分作業+5分休憩」のリズムが守れている目安。"
                   "40分超はぶっ通しのサイン、意識的に席を立つ休憩を")
    else:
        st.info("タスク終了時に「ポモ数」を記入すると、ここにリズム分析が表示されます")

    # ---- 信号別タスク消化率(モード運用の監査) ----
    st.divider()
    st.markdown("#### 🚥 信号別タスク消化率(30日)")
    st.caption("モードルールが機能しているかの監査。"
               "赤の日に低いのは設計通りの休養、青の日まで低ければ別の問題。")
    sig_by_day = {r["date"]: r["総合"] for _, r in cond.iterrows() if r["総合"]}
    rows = []
    for color in ["青", "緑", "黄", "橙", "赤"]:
        rates = [task_rate_by_day[d] for d, s in sig_by_day.items()
                 if s == color and d in task_rate_by_day]
        if rates:
            rows.append((color, sum(rates) / len(rates) * 100, len(rates)))
    if rows:
        cols = st.columns(3)
        for c, (color, avg, n) in zip(cols, rows):
            _ico = {"青": "🔵", "緑": "🟢", "黄": "🟡", "橙": "🟠", "赤": "🔴"}
            c.metric(f"{_ico.get(color, '⚪')} {color}の日 (n={n})", f"{avg:.0f}%")
        fig = go.Figure(go.Bar(
            x=[r[0] for r in rows], y=[r[1] for r in rows],
            marker_color=[SIGNAL_COLOR[r[0]] for r in rows],
            text=[f"{r[1]:.0f}%" for r in rows], textposition="outside"))
        fig.update_layout(height=240, margin=dict(l=10, r=10, t=20, b=10),
                          yaxis=dict(range=[0, 110], title="平均消化率 %"))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("信号とタスクの両方が揃った日が貯まると表示されます")


# ================= 目標 =================
def render_goals():
    tab_run, tab_weight, tab_en, tab_bucket = st.tabs(
        ["🏃 ランニング 100km", "⚖️ 体重・脂質改善", "🇬🇧 英語", "🪣 タイムバケット"])

    with tab_run:
        st.markdown(linked_header("🏃 ランニング記録", URL_RUN_DB), unsafe_allow_html=True)
        total_km = float(runs["km"].sum()) if not runs.empty else 0.0
        elapsed = (today - GOAL_START).days + 1
        period = (GOAL_END - GOAL_START).days + 1
        on_pace_km = GOAL_KM * max(elapsed, 0) / period
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("累計", f"{total_km:.1f} km", f"{total_km - on_pace_km:+.1f} 対計画")
        m2.metric("達成率", f"{total_km / GOAL_KM * 100:.0f}%")
        m3.metric("残り", f"{max(GOAL_KM - total_km, 0):.1f} km")
        m4.metric("必要ペース", f"{max((GOAL_KM - total_km) / max((GOAL_END - today).days, 1) * 7, 0):.1f} km/週")

        if not runs.empty:
            cum = runs.groupby("date")["km"].sum().cumsum().reset_index()
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=cum["date"], y=cum["km"], mode="lines+markers",
                                     name="実績", line=dict(color="#22C55E", width=3)))
            fig.add_trace(go.Scatter(x=[GOAL_START, GOAL_END], y=[0, GOAL_KM], mode="lines",
                                     name="目標 100km", line=dict(color="#6B7280", dash="dash")))
            fig.add_trace(go.Scatter(x=[GOAL_START, GOAL_END], y=[0, STRETCH_KM], mode="lines",
                                     name=f"ストレッチ {STRETCH_KM:.0f}km",
                                     line=dict(color="#EAB308", dash="dot")))
            fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                              legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig, use_container_width=True)

            wk = runs.copy()
            wk["week"] = pd.to_datetime(wk["date"]).dt.to_period("W").dt.start_time
            weekly = wk.groupby("week")["km"].sum().reset_index()
            g1, g2 = st.columns([1, 1.2])
            with g1:
                st.caption("週別距離 (km)")
                st.plotly_chart(go.Figure(go.Bar(x=weekly["week"], y=weekly["km"],
                                                 marker_color="#22C55E"))
                                .update_layout(height=240, margin=dict(l=10, r=10, t=10, b=10)),
                                use_container_width=True)
            with g2:
                st.caption("直近のラン")
                show = runs.sort_values("date", ascending=False).head(8)
                st.dataframe(show[["date", "km", "ペース", "種別", "心拍"]],
                             use_container_width=True, hide_index=True)
        else:
            st.info("期間内のランニング記録がありません")

    with tab_weight:
        w = cond.dropna(subset=["体重"]) if not cond.empty else pd.DataFrame()
        if not w.empty:
            w = w.copy()
            w["移動平均"] = w["体重"].rolling(7, min_periods=3).mean()
            m1, m2, m3 = st.columns(3)
            m1.metric("最新体重", f"{w['体重'].iloc[-1]:.1f} kg")
            waist = cond.dropna(subset=["腹囲"])
            if not waist.empty:
                latest_waist = waist["腹囲"].iloc[-1]
                m2.metric("腹囲", f"{latest_waist:.1f} cm",
                          f"{latest_waist - WAIST_GOAL:+.1f} 対目標{WAIST_GOAL:.0f}cm",
                          delta_color="inverse")
            else:
                m2.metric("腹囲", "—", "測定なし")
            bf = cond.dropna(subset=["体脂肪率"])
            m3.metric("体脂肪率", f"{bf['体脂肪率'].iloc[-1]:.1f} %" if not bf.empty else "—")

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=w["date"], y=w["体重"], mode="markers",
                                     name="実測", marker=dict(color="#6B7280", size=6)))
            fig.add_trace(go.Scatter(x=w["date"], y=w["移動平均"], mode="lines",
                                     name="7日移動平均", line=dict(color="#22C55E", width=3)))
            fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                              legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig, use_container_width=True)
            g1, g2 = st.columns(2)
            with g1:
                if not bf.empty:
                    st.caption("体脂肪率 (%)")
                    st.plotly_chart(line_fig(bf, {"体脂肪率": "#F97316"}, height=220),
                                    use_container_width=True)
            with g2:
                mus = cond.dropna(subset=["骨格筋量"])
                if not mus.empty:
                    st.caption("骨格筋量 (kg)")
                    st.plotly_chart(line_fig(mus, {"骨格筋量": "#3B82F6"}, height=220),
                                    use_container_width=True)

            # ---- 食事: LDL対策の先行指標 ----
            if not meals.empty:
                st.divider()
                st.markdown("##### 🥗 食事(LDL対策の先行指標・7日平均)")
                half = today - dt.timedelta(days=7)
                cols = st.columns(4)
                for c, (name, unit, good_down) in zip(cols, [
                        ("脂質", "g/日", True), ("繊維", "g/日", False),
                        ("塩分", "g/日", True)]):
                    recent = meals[meals["date"] > half][name].dropna()
                    prev = meals[(meals["date"] <= half)][name].dropna()
                    if len(recent) >= 3:
                        d_txt = None
                        if len(prev) >= 3:
                            diff = recent.mean() - prev.mean()
                            d_txt = f"{diff:+.1f} 前週比"
                        c.metric(f"{name} ({unit})", f"{recent.mean():.1f}", d_txt,
                                 delta_color="inverse" if good_down else "normal")
                sc = meals.dropna(subset=["スコア"])
                if not sc.empty:
                    cols[3].metric("健康スコア(7日)",
                                   f"{sc[sc['date'] > half]['スコア'].mean():.0f}"
                                   if len(sc[sc["date"] > half]) else "—")
                trend = meals.dropna(subset=["脂質", "繊維"]).copy()
                if len(trend) >= 7:
                    trend["脂質(7日平均)"] = trend["脂質"].rolling(7, min_periods=3).mean()
                    trend["繊維(7日平均)"] = trend["繊維"].rolling(7, min_periods=3).mean()
                    st.plotly_chart(
                        line_fig(trend, {"脂質(7日平均)": "#F97316",
                                         "繊維(7日平均)": "#22C55E"}, height=220),
                        use_container_width=True)
                    st.caption("狙い: 🟠脂質は下へ、🟢繊維は上へ(医師方針の実行度)")
        else:
            st.info("体重の記録がありません")

    with tab_en:
        st.markdown(linked_header("🇬🇧 英語学習(週目標 7時間)", URL_LEARNING_DB),
                    unsafe_allow_html=True)
        TYPE_COLOR = {"リスニング": "#3B82F6", "スピーキング": "#22C55E",
                      "ライティング": "#F97316", "リーディング": "#14B8A6",
                      "Anki": "#A78BFA", "その他": "#6B7280"}
        learn = pd.DataFrame([
            {
                "date": na.prop_date(p, "日付"),
                "種別": na.prop_select(p, "種別") or "その他",
                "分": (na.prop_number(p, "時間_分")
                       if na.prop_number(p, "時間_分") is not None
                       else measured_minutes(p)) or 0.0,
                "量": na.prop_number(p, "量"),
                "単位": na.prop_select(p, "量単位"),
                "コメント": na.prop_rich_text(p, "成長コメント").strip(),
            }
            for p in data.get("learning", []) if na.prop_date(p, "日付")
        ])
        if learn.empty:
            st.info("学習記録DBにデータが貯まるとここに表示されます"
                    "(リスニング・スピーキング・ライティング・Anki)")
        else:
            learn["date"] = to_jst_date(learn["date"])
            week_start = today - dt.timedelta(days=today.weekday())  # 月曜起点
            this_week = learn[learn["date"] >= week_start]
            wk_min = float(this_week["分"].sum())
            days_left = 7 - today.weekday()
            m1, m2, m3 = st.columns(3)
            m1.metric("今週の合計", f"{wk_min / 60:.1f} h",
                      f"{(wk_min - WEEKLY_EN_MIN) / 60:+.1f} h 対目標")
            m2.metric("達成率", f"{wk_min / WEEKLY_EN_MIN * 100:.0f}%")
            m3.metric("残り", f"{max(WEEKLY_EN_MIN - wk_min, 0) / 60:.1f} h"
                             f"(あと{days_left}日)")
            st.progress(min(wk_min / WEEKLY_EN_MIN, 1.0))

            # 今週の種別内訳
            if not this_week.empty:
                parts = this_week.groupby("種別")["分"].sum()
                st.caption("今週の内訳: " + " / ".join(
                    f"{t} {int(v)}分" for t, v in parts.items() if v > 0))

            # 週別積み上げバー(12週)
            wk = learn.copy()
            wk["week"] = wk["date"].map(lambda d: d - dt.timedelta(days=d.weekday()))
            pivot = wk.pivot_table(index="week", columns="種別", values="分",
                                   aggfunc="sum").fillna(0)
            fig = go.Figure()
            for t in ["リスニング", "スピーキング", "ライティング", "リーディング",
                      "Anki", "その他"]:
                if t in pivot.columns:
                    fig.add_trace(go.Bar(x=pivot.index, y=pivot[t], name=t,
                                         marker_color=TYPE_COLOR[t]))
            fig.add_hline(y=WEEKLY_EN_MIN, line_dash="dash", line_color="#EAB308",
                          annotation_text="週目標 420分")
            fig.update_layout(barmode="stack", height=280,
                              margin=dict(l=10, r=10, t=10, b=10),
                              legend=dict(orientation="h", y=1.12))
            st.plotly_chart(fig, use_container_width=True)

            # ---- 累計学習時間と成長コメント ----
            st.markdown("##### 🏔️ 累計学習時間 — ⭐は成長を実感した日")
            daily_min = learn.groupby("date")["分"].sum().sort_index()
            cum = (daily_min.cumsum() / 60).reset_index()
            cum.columns = ["date", "h"]
            figc = go.Figure()
            figc.add_trace(go.Scatter(
                x=cum["date"], y=cum["h"], mode="lines",
                line=dict(color="#EAB308", width=3), fill="tozeroy",
                hovertext=[f"{r['date']}<br>累計 {r['h']:.1f} h"
                           for _, r in cum.iterrows()],
                hoverinfo="text", name="累計時間"))
            cmts = learn[learn["コメント"] != ""].copy()
            if not cmts.empty:
                cum_map = dict(zip(cum["date"], cum["h"]))
                cmts["h"] = cmts["date"].map(cum_map)
                figc.add_trace(go.Scatter(
                    x=cmts["date"], y=cmts["h"], mode="markers",
                    marker=dict(size=13, color="#EAB308", symbol="star",
                                line=dict(color="#fff", width=1)),
                    hovertext=[
                        f"<b>{r['date']} ({r['種別']})</b><br>💬 "
                        + (r["コメント"][:120] + "…" if len(r["コメント"]) > 120
                           else r["コメント"])
                        for _, r in cmts.iterrows()],
                    hoverinfo="text", name="成長コメント"))
            figc.update_layout(height=260, margin=dict(l=10, r=10, t=10, b=10),
                               legend=dict(orientation="h", y=1.14),
                               yaxis_title="累計時間 (h)")
            st.plotly_chart(figc, use_container_width=True)
            if not cmts.empty:
                last_c = cmts.sort_values("date").iloc[-1]
                st.markdown(
                    f"<div style='padding:0.7rem 1rem;border-radius:12px;background:#161B22;"
                    f"border-left:4px solid #EAB308'>"
                    f"<div style='color:#9CA3AF;font-size:0.75rem'>"
                    f"直近の成長実感 — {last_c['date']} ({last_c['種別']})</div>"
                    f"<div style='margin-top:0.2rem'>{last_c['コメント']}</div></div>",
                    unsafe_allow_html=True)
            else:
                st.caption("「成長コメント」に実感を書いた日が ⭐ として線の上に残ります")

        # ---- 多読: 累計語数(KPI) ----
        st.markdown(f"##### 📚 多読 累計語数 [多読記録↗]({URL_TADOKU_DB})")
        books = pd.DataFrame([
            {
                "date": na.prop_date(p, "読了"),
                "words": na.prop_number(p, "文字") or 0,
                "name": na.prop_title(p, "名前"),
                "level": na.prop_select(p, "レベル") or "",
                "comment": na.prop_rich_text(p, "感想"),
            }
            for p in alltime.get("tadoku_all", []) if na.prop_date(p, "読了")
        ])
        if books.empty:
            st.info("読了した本が記録されるとここに累計語数が積み上がります")
        else:
            books["date"] = to_jst_date(books["date"])
            books = books.sort_values("date").reset_index(drop=True)
            books["cum"] = books["words"].cumsum()

            q1, q2 = st.columns(2)
            q1.metric("読了", f"{len(books)} 冊")
            q2.metric("累計語数", f"{books['cum'].iloc[-1]:,.0f} 語")

            def _wrap(text: str, limit: int = 90) -> str:
                text = (text or "").replace("\n", " ")
                return text[:limit] + "…" if len(text) > limit else (text or "(感想なし)")

            hover = [
                f"<b>{r['name']}</b> ({r['level']})<br>"
                f"{r['date']} 読了 / {r['words']:,.0f} 語<br>"
                f"累計 {r['cum']:,.0f} 語<br>"
                f"<i>💬 {_wrap(r['comment'])}</i>"
                for _, r in books.iterrows()
            ]
            fig = go.Figure(go.Scatter(
                x=books["date"], y=books["cum"], mode="lines+markers",
                line=dict(color="#F97316", width=3),
                marker=dict(size=10, color="#F97316",
                            line=dict(color="#fff", width=1)),
                hovertext=hover, hoverinfo="text"))
            fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                              yaxis_title="累計語数")
            st.plotly_chart(fig, use_container_width=True)
            st.caption("点にタッチ/ホバーすると、その本の感想が読めます")

            # 直近の1冊の感想を常時表示(成長ログと同じ再掲示の思想)
            last = books.iloc[-1]
            if last["comment"]:
                st.markdown(
                    f"<div style='padding:0.7rem 1rem;border-radius:12px;background:#161B22;"
                    f"border-left:4px solid #F97316'>"
                    f"<div style='color:#9CA3AF;font-size:0.75rem'>"
                    f"直近の読了: {last['name']} ({last['date']})</div>"
                    f"<div style='margin-top:0.2rem'>{last['comment']}</div></div>",
                    unsafe_allow_html=True)

        # ---- その他の量(観察指標) ----
        if not learn.empty:
            anki_wk = learn[(learn["種別"] == "Anki") &
                            (learn["date"] >= today - dt.timedelta(days=7))]
            lessons = learn[(learn["種別"] == "スピーキング") &
                            (learn["date"] >= today - dt.timedelta(days=28))]
            q1, q2, _sp = st.columns(3)
            q1.metric("Anki 復習(7日)", f"{anki_wk['量'].sum():.0f} 枚")
            q2.metric("レッスン(28日)", f"{lessons['量'].sum():.0f} 回")
        st.caption("※ リーディングの時間は学習記録(開始/終了ボタン計測)で週7時間に含まれます。"
                   "語数は多読記録DBで管理(読了時に入力)")

    with tab_bucket:
        URL_BUCKET_DB = "https://app.notion.com/p/1ca6e5b9ef1080489650cdbdb9e9cb99"
        BIRTHDATE: dt.date | None = None  # 例: dt.date(1992, 4, 1) に書き換えて使う
        LIFE_END_AGE = 80
        BUCKET_ORDER = ["0~9歳", "10~15歳", "16~20歳", "21~25歳", "26~30歳",
                        "31~35歳", "36~40歳", "41~50歳", "51~60歳", "61~70歳", "71~80歳"]
        CURRENT_BUCKET = "31~35歳"
        STATUS_COLOR = {"✓達成済み": "#22C55E", "計画完了": "#F97316",
                        "計画中": "#3B82F6", "未着手": "#6B7280"}

        # ---- 残り時間 ----
        year_end = dt.date(today.year, 12, 31)
        days_left_year = (year_end - today).days
        r1, r2, r3 = st.columns(3)
        r1.metric(f"今年({today.year})の残り", f"{days_left_year} 日",
                  f"{days_left_year / 7:.0f} 週")
        if BIRTHDATE:
            try:
                end_day = BIRTHDATE.replace(year=BIRTHDATE.year + LIFE_END_AGE)
            except ValueError:  # 2/29生まれ対応
                end_day = dt.date(BIRTHDATE.year + LIFE_END_AGE, 3, 1)
            days_left_life = max((end_day - today).days, 0)
            r2.metric(f"{LIFE_END_AGE}歳まで", f"{days_left_life / 365.25:.1f} 年",
                      f"{days_left_life:,} 日")
            try:
                bucket_end = BIRTHDATE.replace(year=BIRTHDATE.year + 36)
            except ValueError:
                bucket_end = dt.date(BIRTHDATE.year + 36, 3, 1)
            r3.metric("現バケット(〜36歳)の残り",
                      f"{max((bucket_end - today).days, 0):,} 日")
        else:
            r2.metric(f"{LIFE_END_AGE}歳まで", "—")
            r2.caption("app.py の BIRTHDATE を設定すると表示されます")
        st.divider()

        tb = pd.DataFrame([
            {
                "name": na.prop_title(p, "やりたいこと"),
                "status": na.prop_status(p, "Status"),
                "bucket": na.prop_select(p, "年代"),
                "who": na.prop_multi_select(p, "対象"),
            }
            for p in data["timebucket"]
        ])
        st.markdown(linked_header("🪣 Time Bucket", URL_BUCKET_DB), unsafe_allow_html=True)
        if tb.empty:
            st.info("Time Bucketに項目がありません")
        else:
            who = st.radio("対象", ["全体", "智博", "早紀", "家族"],
                           horizontal=True, key="tb_who")
            view = tb if who == "全体" else tb[tb["who"].apply(lambda w: who in w)]
            active = view[view["status"] != "取り下げ"]  # 取り下げは分母から除外

            n_all = len(active)
            n_done = int((active["status"] == "✓達成済み").sum())
            cur = active[active["bucket"] == CURRENT_BUCKET]
            cur_done = int((cur["status"] == "✓達成済み").sum())
            cur_planned = int(cur["status"].isin(["計画中", "計画完了"]).sum())

            m1, m2, m3 = st.columns(3)
            m1.metric("生涯達成率", f"{n_done}/{n_all}",
                      f"{n_done / n_all * 100:.0f}%" if n_all else None)
            m2.metric(f"現バケット({CURRENT_BUCKET})",
                      f"{cur_done}/{len(cur)} 達成")
            m3.metric("同バケット 計画進行中", f"{cur_planned} 件")
            if len(cur):
                st.progress(cur_done / len(cur))
                remain = cur[~cur["status"].isin(["✓達成済み"])]
                if not remain.empty:
                    st.caption("31~35歳のうちに: " +
                               " / ".join(remain["name"].head(4)))

            # 年代別の積み上げバー
            counts = (active.groupby(["bucket", "status"]).size()
                      .unstack(fill_value=0)
                      .reindex(BUCKET_ORDER).fillna(0))
            fig = go.Figure()
            for status in ["✓達成済み", "計画完了", "計画中", "未着手"]:
                if status in counts.columns:
                    fig.add_trace(go.Bar(x=counts.index, y=counts[status],
                                         name=status,
                                         marker_color=STATUS_COLOR[status]))
            fig.update_layout(barmode="stack", height=300,
                              margin=dict(l=10, r=10, t=10, b=10),
                              legend=dict(orientation="h", y=1.12))
            st.plotly_chart(fig, use_container_width=True)


# ================= 習慣詳細 =================
def render_habits():
    st.markdown("#### ✅ 習慣詳細(30日)")
    st.caption(f"[📝 日次ログDB]({URL_DAILY_DB}) / [🧘 瞑想記録DB]({URL_MED_DB}) / "
               f"[🤸 ストレッチ=日次ログ内]({URL_DAILY_DB})")
    last30 = sorted(today - dt.timedelta(days=i) for i in range(30))
    habits = [
        ("📝 日次ログ", log_dates, "#22C55E"),
        ("🧘 瞑想", med_dates, "#A78BFA"),
        ("🤸 ストレッチ", stretch_dates, "#3B82F6"),
    ]
    cols = st.columns(3)
    alltime_map = {"📝 日次ログ": TOTALS["log"], "🧘 瞑想": TOTALS["med"]}
    for c, (label, dates, _) in zip(cols, habits):
        rate = sum(1 for d in last30 if d in dates)
        c.metric(label, f"{streak_from(dates, today)} 日連続", f"30日で {rate}/30")
        if label in alltime_map:
            total, best = alltime_map[label]
            extra = ""
            if label == "🧘 瞑想" and med_total_min:
                m30 = sum(v for d, v in med_min_by_day.items() if d in last30)
                extra = f"・30日 {m30:.0f} 分・累計 {med_total_min / 60:.1f} 時間"
            c.caption(f"通算 {total} 日・最長 {best} 日{extra}")
    st.divider()
    for label, dates, color in habits:
        st.markdown(heat_row(label.split(" ")[1], last30, dates, color),
                    unsafe_allow_html=True)
    st.caption("左が30日前、右が今日")

    # ---- 復帰力(レジリエンス) ----
    st.divider()
    st.markdown("#### 🔄 復帰力 — 途切れても、戻ってきた回数")
    rows_cb = []
    for label, dates in [("📝 日次ログ", _all_log_dates), ("🧘 瞑想", _all_med_dates)]:
        cb = comeback_stats(dates, today)
        if cb["n"]:
            rows_cb.append((label, cb))
    if rows_cb:
        cols = st.columns(len(rows_cb))
        for c, (label, cb) in zip(cols, rows_cb):
            c.metric(f"{label} 復帰回数", f"{cb['n']} 回",
                     f"平均 {cb['avg']:.1f} 日で復帰(最長 {cb['max']} 日)",
                     delta_color="off")
        st.caption("途切れた回数 = 戻ってきた回数。ストリークが切れることより、"
                   "何度でも戻れることの方が長期では効く")
    else:
        st.caption("🔄 復帰力: まだ途切れの記録なし(=完走中)")

    # ---- 日別の瞑想分数(30日) ----
    if med_min_by_day:
        st.divider()
        st.markdown("#### 🧘 瞑想分数(30日)")
        bars = pd.DataFrame({"date": last30,
                             "分": [med_min_by_day.get(d, 0.0) for d in last30]})
        fig = go.Figure(go.Bar(x=bars["date"], y=bars["分"], marker_color="#A78BFA"))
        avg_min = bars[bars["分"] > 0]["分"].mean()
        if pd.notna(avg_min):
            fig.add_hline(y=avg_min, line_dash="dash", line_color="#9CA3AF",
                          annotation_text=f"実施日平均 {avg_min:.0f}分")
        fig.update_layout(height=220, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

    # ---- 瞑想との関係(比較分析) ----
    st.divider()
    st.markdown("#### 🧘 瞑想との関係(30日)")

    def compare_by_meditation(series_by_day: dict, label: str, unit: str = "",
                              good_down: bool = True, chart_color: str = "#EF4444"):
        """日付→値 の辞書を瞑想日/非瞑想日で比較表示する共通部品。"""
        sdf = pd.DataFrame(sorted(series_by_day.items()), columns=["date", "v"]).dropna()
        sdf["瞑想"] = sdf["date"].isin(med_dates)
        if len(sdf) < 5 or sdf["瞑想"].sum() == 0:
            st.info("比較にはもう少しデータの蓄積が必要です")
            return
        med_avg = sdf[sdf["瞑想"]]["v"].mean()
        no_avg = sdf[~sdf["瞑想"]]["v"].mean()
        diff = med_avg - no_avg
        improved = (diff < 0) if good_down else (diff > 0)
        m1, m2, m3 = st.columns(3)
        m1.metric(f"瞑想した日 (n={int(sdf['瞑想'].sum())})", f"{med_avg:.1f}{unit}")
        m2.metric(f"しなかった日 (n={int((~sdf['瞑想']).sum())})", f"{no_avg:.1f}{unit}")
        m3.metric("差", f"{diff:+.1f}{unit}", "✨" if improved else "", delta_color="off")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=sdf["date"], y=sdf["v"], mode="lines",
                                 name=label, line=dict(color=chart_color, width=2)))
        md = sdf[sdf["瞑想"]]
        fig.add_trace(go.Scatter(x=md["date"], y=md["v"], mode="markers",
                                 name="瞑想した日",
                                 marker=dict(color="#A78BFA", size=11,
                                             line=dict(color="#fff", width=1))))
        fig.update_layout(height=260, margin=dict(l=10, r=10, t=10, b=10),
                          legend=dict(orientation="h", y=1.14))
        st.plotly_chart(fig, use_container_width=True)

    if not cond.empty:
        t_stress, t_hrv, t_task, t_habit = st.tabs(
            ["😤 ストレス", "💓 翌朝HRV", "📋 タスク消化率", "✅ 習慣"])

        with t_stress:
            st.caption("「前日平均ストレス」を1日シフトし、瞑想したその日のストレスと比較")
            stress_by_day = {r["date"] - dt.timedelta(days=1): r["ストレス"]
                             for _, r in cond.iterrows() if pd.notna(r["ストレス"])}
            compare_by_meditation(stress_by_day, "日中平均ストレス", good_down=True)

        with t_hrv:
            st.caption("瞑想した日の「翌朝」の夜間HRVと比較(高いほど回復)")
            hrv_next = {r["date"] - dt.timedelta(days=1): r["HRV"]
                        for _, r in cond.iterrows() if pd.notna(r["HRV"])}
            if drink_dates and st.checkbox("🍺 飲酒日を除外(交絡除去)", value=True,
                                           key="hrv_ex_alc"):
                hrv_next = {d: v for d, v in hrv_next.items() if d not in drink_dates}
            compare_by_meditation(hrv_next, "翌朝の夜間HRV", unit=" ms",
                                  good_down=False, chart_color="#22C55E")

        with t_task:
            st.caption("その日に生成されたタスクの消化率(未実施=やらない判断も消化に含む)")
            rate_pct = {d: v * 100 for d, v in task_rate_by_day.items()}
            compare_by_meditation(rate_pct, "タスク消化率", unit=" %",
                                  good_down=False, chart_color="#3B82F6")

        with t_habit:
            st.caption("瞑想した日は他の習慣もできているか(習慣の連鎖)")
            days_obs = [d for d in last30 if d in log_dates or d in med_dates or d in stretch_dates]
            med_days30 = [d for d in last30 if d in med_dates]
            non_days30 = [d for d in last30 if d not in med_dates]
            rows = []
            for name, hits in [("日次ログ記入", log_dates), ("ストレッチ", stretch_dates)]:
                m_rate = sum(1 for d in med_days30 if d in hits) / len(med_days30) * 100 if med_days30 else None
                n_rate = sum(1 for d in non_days30 if d in hits) / len(non_days30) * 100 if non_days30 else None
                rows.append((name, m_rate, n_rate))
            c1, c2 = st.columns(2)
            for c, (name, m_rate, n_rate) in zip((c1, c2), rows):
                if m_rate is not None and n_rate is not None:
                    c.metric(f"{name}率", f"{m_rate:.0f}% / {n_rate:.0f}%",
                             f"瞑想日 vs 非瞑想日 ({m_rate - n_rate:+.0f}pt)",
                             delta_color="off")
        st.caption("※ いずれも相関の観察であって因果の証明ではありません"
                   "(余裕のある日に瞑想も他の行動もできる、という共通原因が典型です)")


# ================= 成長 =================
def render_growth():
    st.markdown("#### 🌱 成長 — 過去の自分との比較")
    period = st.radio("比較期間", ["30日", "90日", "180日", "365日"],
                      horizontal=True, key="growth_period")
    P = {"30日": 30, "90日": 90, "180日": 180, "365日": 365}[period]
    cut1 = today - dt.timedelta(days=P)       # 直近期間の起点
    cut2 = today - dt.timedelta(days=2 * P)   # 前期間の起点

    def window_mean(df, col, lo, hi):
        if df.empty:
            return None
        vals = df[(df["date"] > lo) & (df["date"] <= hi)][col].dropna()
        return vals.mean() if len(vals) >= 3 else None

    # --- 比較チップ(直近P日 vs その前P日) ---
    chips = []
    for name, unit, good_down in [("RHR", "bpm", True), ("HRV", "ms", False),
                                   ("睡眠スコア", "", False), ("体重", "kg", True),
                                   ("体脂肪率", "%", True), ("骨格筋量", "kg", False)]:
        r = window_mean(cond_all, name, cut1, today)
        p = window_mean(cond_all, name, cut2, cut1)
        if r is not None and p is not None:
            diff = r - p
            chips.append((name, f"{diff:+.1f} {unit}".strip(),
                          (diff < 0) if good_down else (diff > 0)))
    # ランペース・週間距離
    if not runs_all.empty:
        pr = runs_all[(runs_all["date"] > cut1)]["pace_sec"].dropna()
        pp = runs_all[(runs_all["date"] > cut2) & (runs_all["date"] <= cut1)]["pace_sec"].dropna()
        if len(pr) >= 3 and len(pp) >= 3:
            diff = pr.mean() - pp.mean()
            chips.append(("平均ペース",
                          f"{'+' if diff >= 0 else '−'}{fmt_pace(abs(diff))}/km", diff < 0))
        kr = runs_all[(runs_all["date"] > cut1)]["km"].sum() / P * 7
        kp = runs_all[(runs_all["date"] > cut2) & (runs_all["date"] <= cut1)]["km"].sum() / P * 7
        if kr or kp:
            chips.append(("走行 km/週", f"{kr - kp:+.1f}", (kr - kp) > 0))
    # 瞑想 分/週
    mr = sum(v for d, v in med_min_by_day.items() if d > cut1) / P * 7
    mp = sum(v for d, v in med_min_by_day.items() if cut2 < d <= cut1) / P * 7
    if mr or mp:
        chips.append(("瞑想 分/週", f"{mr - mp:+.0f}", (mr - mp) > 0))

    if chips:
        st.caption(f"直近{P}日 vs その前{P}日(✨=改善)")
        for i in range(0, len(chips), 4):
            cols = st.columns(4)
            for c, (name, val, improved) in zip(cols, chips[i:i + 4]):
                c.metric(name, val + (" ✨" if improved else ""))
    else:
        st.info("この期間の比較にはまだデータが足りません")

    # --- 積み上げ資産(絶対に減らない数字) ---
    st.divider()
    st.markdown("#### 🏔️ 積み上げ資産")
    a1, a2, a3, a4, a5 = st.columns(5)
    a1.metric("生涯走行", f"{lifetime_km:.0f} km")
    a2.metric("瞑想 累計", f"{med_total_min / 60:.1f} h")
    a3.metric("日次ログ 通算", f"{TOTALS['log'][0]} 日")
    a4.metric("最長ストリーク", f"{max(TOTALS['log'][1], TOTALS['med'][1])} 日")
    n_books = len([p for p in alltime.get("tadoku_all", []) if na.prop_date(p, "読了")])
    a5.metric("読了", f"{n_books} 冊")

    g1, g2 = st.columns(2)
    with g1:
        if not runs_all.empty:
            cum = runs_all.groupby("date")["km"].sum().cumsum().reset_index()
            st.caption("累計走行距離 (km)")
            fig = go.Figure(go.Scatter(x=cum["date"], y=cum["km"], mode="lines",
                                       line=dict(color="#22C55E", width=3),
                                       fill="tozeroy"))
            fig.update_layout(height=230, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)
    with g2:
        if med_min_by_day:
            md = pd.DataFrame(sorted(med_min_by_day.items()), columns=["date", "分"])
            md["累計h"] = md["分"].cumsum() / 60
            st.caption("瞑想 累計時間 (h)")
            fig = go.Figure(go.Scatter(x=md["date"], y=md["累計h"], mode="lines",
                                       line=dict(color="#A78BFA", width=3),
                                       fill="tozeroy"))
            fig.update_layout(height=230, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)

    # --- タスクの積み上げ(90日) ---
    st.divider()
    st.markdown("#### 📋 タスクの積み上げ")
    if _task_daily:
        done_by_day = pd.Series(task_done_by_day).sort_index()
        total_done = int(done_by_day.sum())
        t1, t2, t3 = st.columns(3)
        t1.metric("完了タスク(90日)", f"{total_done} 件")
        t2.metric("今週の完了", f"{TW_DONE} 件",
                  f"{TW_DONE - LW_DONE_SAME:+d} vs 先週同時点", delta_color="normal")
        tw_rate = TW_DONE / TW_TOT * 100 if TW_TOT else None
        lw_rate = LW_DONE_FULL / LW_TOT_FULL * 100 if LW_TOT_FULL else None
        if tw_rate is not None:
            t3.metric("今週の完了率", f"{tw_rate:.0f}%",
                      f"{tw_rate - lw_rate:+.0f}pt vs 先週" if lw_rate else None,
                      delta_color="normal")

        # 累計完了の面グラフ(絶対に減らない線)
        cum_done = done_by_day.cumsum().reset_index()
        cum_done.columns = ["date", "cum"]
        fig = go.Figure(go.Scatter(
            x=cum_done["date"], y=cum_done["cum"], mode="lines",
            line=dict(color="#22C55E", width=3), fill="tozeroy",
            hovertext=[f"{r['date']}<br>累計 {r['cum']:.0f} 件"
                       for _, r in cum_done.iterrows()],
            hoverinfo="text"))
        fig.update_layout(height=240, margin=dict(l=10, r=10, t=10, b=10),
                          yaxis_title="累計完了 (件)")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("この線は絶対に下がらない。片づけた一つひとつが積もっていく")

        # 今週のジャンル内訳(完了のみ)
        genre = {}
        for p in alltime.get("tasks_30d", []):
            d = na.prop_date(p, "実行日時")
            if not d or _to_date(d) < _wk_start:
                continue
            if na.prop_status(p, "ステータス") in TASK_OPEN:
                continue
            g = na.prop_select(p, "ジャンル") or "未分類"
            genre[g] = genre.get(g, 0) + 1
        if genre:
            gs = sorted(genre.items(), key=lambda x: -x[1])
            figg = go.Figure(go.Bar(
                x=[v for _, v in gs], y=[k for k, _ in gs],
                orientation="h", marker_color="#3B82F6",
                text=[f"{v}件" for _, v in gs], textposition="outside"))
            figg.update_layout(height=max(160, 40 * len(gs)),
                               margin=dict(l=10, r=10, t=26, b=10),
                               title=dict(text="今週なにを片づけたか(ジャンル別)",
                                          font=dict(size=13)),
                               xaxis=dict(range=[0, max(v for _, v in gs) * 1.25]))
            st.plotly_chart(figg, use_container_width=True)
    else:
        st.info("タスクの実行データが貯まると表示されます")

    # --- 長期の身体トレンド(7日移動平均) ---
    st.divider()
    st.markdown(f"#### 📈 身体トレンド(直近{P}日・7日移動平均)")
    win = cond_all[cond_all["date"] > cut1].copy() if not cond_all.empty else pd.DataFrame()
    if len(win) >= 7:
        for col in ["RHR", "HRV", "睡眠スコア"]:
            win[col + "_ma"] = win[col].rolling(7, min_periods=3).mean()
        t1, t2 = st.columns(2)
        with t1:
            st.caption("安静時心拍 RHR(低いほど良い)")
            st.plotly_chart(line_fig(win.rename(columns={"RHR_ma": "RHR 7日平均"}),
                                     {"RHR 7日平均": "#F97316"}, height=220),
                            use_container_width=True)
        with t2:
            st.caption("夜間HRV(高いほど良い)")
            st.plotly_chart(line_fig(win.rename(columns={"HRV_ma": "HRV 7日平均"}),
                                     {"HRV 7日平均": "#22C55E"}, height=220),
                            use_container_width=True)
    else:
        st.info("トレンド表示にはもう少しデータが必要です")

    # --- 月別の習慣継続 ---
    st.divider()
    st.markdown("#### 📅 月別の継続(直近12ヶ月)")
    months = pd.period_range(end=pd.Timestamp(today), periods=12, freq="M")
    rows = {"月": [str(m) for m in months]}
    for label, dates in [("日次ログ", _all_log_dates), ("瞑想", _all_med_dates)]:
        rows[label] = [sum(1 for d in dates if pd.Period(d, freq="M") == m)
                       for m in months]
    mdf = pd.DataFrame(rows)
    fig = go.Figure()
    fig.add_trace(go.Bar(x=mdf["月"], y=mdf["日次ログ"], name="日次ログ",
                         marker_color="#22C55E"))
    fig.add_trace(go.Bar(x=mdf["月"], y=mdf["瞑想"], name="瞑想",
                         marker_color="#A78BFA"))
    fig.update_layout(barmode="group", height=260,
                      margin=dict(l=10, r=10, t=10, b=10),
                      legend=dict(orientation="h", y=1.12),
                      yaxis_title="実施日数/月")
    st.plotly_chart(fig, use_container_width=True)
    st.caption("棒が並び続けること自体が成果。完璧な月である必要はありません")

    # --- 思考の天気(嵐は過ぎる) ---
    st.divider()
    st.markdown("#### 🌊 思考の天気 — 嵐は過ぎる")
    if not at_df.empty:
        strong = at_df[at_df["強度"].fillna(0) >= 4]
        last_storm = strong["date"].max() if not strong.empty else None
        calm_days = (today - last_storm).days if last_storm else None
        n30 = len(at_df[at_df["date"] > today - dt.timedelta(days=30)])
        n30p = len(at_df[(at_df["date"] > today - dt.timedelta(days=60)) &
                         (at_df["date"] <= today - dt.timedelta(days=30))])
        w1, w2, w3 = st.columns(3)
        if calm_days is not None:
            w1.metric("強い思考(強度4+)から", f"{calm_days} 日",
                      "凪が続いています" if calm_days >= 7 else None,
                      delta_color="off")
        w2.metric("直近30日の記録", f"{n30} 件",
                  f"{n30 - n30p:+d} vs その前30日", delta_color="off")
        w3.metric("通算の観察数", f"{len(at_df)} 件")

        # 嵐マップ: 全期間の強度散布図
        colors = at_df["強度"].fillna(0).map(
            lambda s: "#EF4444" if s >= 4 else "#F97316" if s >= 3 else "#6B7280")
        fig = go.Figure(go.Scatter(
            x=at_df["date"], y=at_df["強度"], mode="markers",
            marker=dict(size=9, color=list(colors),
                        line=dict(color="#0B0F14", width=1)),
            hovertext=[f"{r['date']} 強度{r['強度']:.0f}" for _, r in at_df.iterrows()],
            hoverinfo="text"))
        fig.update_layout(height=240, margin=dict(l=10, r=10, t=10, b=10),
                          yaxis=dict(title="感情強度", range=[-0.4, 5.4]))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("赤い点(強い嵐)は続かない。点と点の間の空白こそが、記録が教えてくれる事実")

        # 月別バイアスの内訳(12ヶ月)
        atb = at_df[at_df["date"] > today - dt.timedelta(days=365)].explode("バイアス").dropna(subset=["バイアス"])
        if not atb.empty:
            atb["month"] = atb["date"].map(lambda d: f"{d.year}-{d.month:02d}")
            pv = atb.pivot_table(index="month", columns="バイアス",
                                 values="date", aggfunc="count").fillna(0)
            figb = go.Figure()
            for col in pv.columns:
                figb.add_trace(go.Bar(x=pv.index, y=pv[col], name=col))
            figb.update_layout(barmode="stack", height=240,
                               margin=dict(l=10, r=10, t=10, b=10),
                               legend=dict(orientation="h", y=1.15),
                               yaxis_title="件/月")
            st.plotly_chart(figb, use_container_width=True)

        # 過ぎ去った嵐(30日以上前・強度4+・再解釈あり のみ再掲示)
        past_storms = [r for _, r in strong.iterrows()
                       if r["再解釈"] and r["date"] <= today - dt.timedelta(days=30)]
        if past_storms:
            import random
            r = random.Random(today.toordinal() + 3).choice(past_storms)
            naiyo = r["内容"][:80] + ("…" if len(r["内容"]) > 80 else "")
            saikai = r["再解釈"][:160] + ("…" if len(r["再解釈"]) > 160 else "")
            st.markdown(
                f"<div style='padding:0.9rem 1.1rem;border-radius:12px;background:#161B22;"
                f"border-left:4px solid #3B82F6'>"
                f"<div style='color:#9CA3AF;font-size:0.75rem'>"
                f"⛈️ 過ぎ去った嵐 — {r['date']} ({(today - r['date']).days}日前・強度{r['強度']:.0f})</div>"
                f"<div style='color:#9CA3AF;font-size:0.9rem;margin-top:0.25rem;"
                f"text-decoration:line-through'>{naiyo}</div>"
                f"<div style='margin-top:0.35rem'>💡 {saikai}</div></div>",
                unsafe_allow_html=True)
            st.caption("あのとき最強度だった思考も、いまは再解釈と一緒に過去にある")
    else:
        st.info("自動思考カウントの記録が貯まると、ここに「嵐は過ぎる」の証拠が積み上がります")

    # --- あの日の自分から(2枚) ---
    st.divider()
    st.markdown("#### 💬 あの日の自分から")
    import random
    r = random.Random(today.toordinal() + 1)  # 今日ページとは別の種
    c1, c2 = st.columns(2)
    g_pool = [(d, t) for d, t in growth_entries]
    if g_pool:
        d, t = r.choice(g_pool)
        c1.markdown(
            f"<div style='padding:0.8rem 1rem;border-radius:12px;background:#161B22;"
            f"border-left:4px solid #22C55E'>"
            f"<div style='color:#9CA3AF;font-size:0.75rem'>🌱 {d} ({(today - d).days}日前)</div>"
            f"<div style='margin-top:0.2rem'>{t}</div></div>", unsafe_allow_html=True)
    if hansho_entries:
        h = r.choice(hansho_entries)
        learning = h["learning"][:150] + ("…" if len(h["learning"]) > 150 else "")
        c2.markdown(
            f"<div style='padding:0.8rem 1rem;border-radius:12px;background:#161B22;"
            f"border-left:4px solid #A78BFA'>"
            f"<div style='color:#9CA3AF;font-size:0.75rem'>🛡️ {h['category']} — {h['date']}</div>"
            f"<div style='font-weight:600;margin-top:0.2rem'>{h['event']}</div>"
            f"<div style='color:#D1D5DB;font-size:0.9rem;margin-top:0.2rem'>{learning}</div></div>",
            unsafe_allow_html=True)


# ================= 羅針盤 =================
URL_COMPASS_DB = "https://app.notion.com/p/d3d820cabc89486bbc2051bd77a07e24"
URL_HANSHO_DB = "https://app.notion.com/p/f9f0d1476b1d451088407926e57fe385"


def render_compass():
    rows = [
        {
            "name": na.prop_title(p, "名前"),
            "kind": na.prop_select(p, "種別"),
            "line": na.prop_rich_text(p, "一行"),
            "order": na.prop_number(p, "順序") or 999,
            "url": p.get("url", URL_COMPASS_DB),
        }
        for p in alltime.get("compass_all", [])
    ]
    if not rows:
        st.info("羅針盤DBが未接続か空です。DBにclaude-agentの接続を追加してください。"
                "接続済みの場合はキャッシュが古い可能性があります(全期間データは1時間毎)")
        if st.button("🔄 データを再取得", key="compass_refresh"):
            st.cache_data.clear()
            st.rerun()
        return
    rows.sort(key=lambda r: r["order"])
    values = [r for r in rows if r["kind"] == "価値観"]
    questions = [r for r in rows if r["kind"] == "毎日の問い"]
    concepts = [r for r in rows if r["kind"] == "概念再定義"]

    import random
    rnd = random.Random(today.toordinal() + 7)

    # --- 今日の問い(日替わり1問) ---
    if questions:
        q = questions[today.toordinal() % len(questions)]  # 順繰りで全問回る
        st.markdown(
            f"<div style='padding:1.2rem 1.4rem;border-radius:14px;background:#161B22;"
            f"border:1px solid #30363D;text-align:center'>"
            f"<div style='color:#9CA3AF;font-size:0.75rem'>今日の問い</div>"
            f"<div style='font-size:1.35rem;font-weight:700;margin-top:0.3rem'>"
            f"{q['line']}</div></div>",
            unsafe_allow_html=True)

    # --- 価値観カード ---
    st.markdown(f"##### 🧭 価値観 [DB↗]({URL_COMPASS_DB})")
    cols = st.columns(len(values) or 1)
    for c, v in zip(cols, values):
        name = v["name"].split("｜")[0]
        label = v["name"].split("｜")[1] if "｜" in v["name"] else ""
        c.markdown(
            f"<a href='{v['url']}' target='_blank' style='text-decoration:none'>"
            f"<div style='padding:0.7rem;border-radius:12px;background:#161B22;"
            f"border:1px solid #30363D;height:100%'>"
            f"<div style='font-weight:700'>{name}</div>"
            f"<div style='color:#9CA3AF;font-size:0.7rem'>{label}</div>"
            f"<div style='font-size:0.82rem;margin-top:0.35rem;color:#D1D5DB'>"
            f"{v['line']}</div></div></a>",
            unsafe_allow_html=True)

    # --- ライフストーリー(人生の折れ線) ---
    URL_LIFESTORY = "https://app.notion.com/p/37a6e5b9ef1081d8a2fbf194506ada45"
    URL_LIFE_DB = "https://app.notion.com/p/09f714353f5248deabcb69660f0151f0"
    life = pd.DataFrame([
        {
            "date": na.prop_date(p, "時期"),
            "score": na.prop_number(p, "スコア"),
            "name": na.prop_title(p, "名前"),
            "line": na.prop_rich_text(p, "一行"),
            "url": p.get("url", URL_LIFE_DB),
        }
        for p in alltime.get("life_all", []) if na.prop_date(p, "時期")
    ])
    if not life.empty:
        life["date"] = life["date"].map(_to_date)
        life = life.dropna(subset=["score"]).sort_values("date")
        st.markdown(f"##### 📖 ライフストーリー "
                    f"<a href='{URL_LIFESTORY}' target='_blank' "
                    f"style='font-size:0.7em;color:#3B82F6;text-decoration:none'>"
                    f"物語を読む ↗</a>　"
                    f"<a href='{URL_LIFE_DB}' target='_blank' "
                    f"style='font-size:0.7em;color:#9CA3AF;text-decoration:none'>"
                    f"イベントDB ↗</a>", unsafe_allow_html=True)
        hover = [
            f"<b>{r['name']}</b> ({r['date'].year})<br>スコア {r['score']:+.0f}"
            + (f"<br><i>💡 {r['line']}</i>" if r["line"] else "")
            for _, r in life.iterrows()
        ]
        colors = ["#22C55E" if s >= 0 else "#F97316" for s in life["score"]]
        fig = go.Figure(go.Scatter(
            x=life["date"], y=life["score"], mode="lines+markers",
            line=dict(color="#6B7280", width=2),
            marker=dict(size=10, color=colors, line=dict(color="#0B0F14", width=1)),
            hovertext=hover, hoverinfo="text"))
        fig.add_hline(y=0, line_color="#30363D")
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                          yaxis=dict(title="満足度", range=[-5.6, 5.6],
                                     tickvals=[-5, 0, 5]))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("どの谷にも、引き上げたものがあった(点にタッチすると表示)。"
                   "スコアや出来事の見直しはイベントDBから")

    # --- 概念の再定義(反証体験の裏打ち件数つき) ---
    st.markdown(f"##### 🎴 9つの概念 — 揺らいだときに読む "
               f"<a href='{URL_HANSHO_DB}' target='_blank' "
               f"style='font-size:0.7em;color:#A78BFA;text-decoration:none'>"
               f"🛡️ 反証体験ログを開く ↗</a>", unsafe_allow_html=True)
    concept_counts = {}
    for h in hansho_entries:
        for cpt in h.get("concepts", []):
            concept_counts[cpt] = concept_counts.get(cpt, 0) + 1
    for i in range(0, len(concepts), 3):
        cols = st.columns(3)
        for c, cpt in zip(cols, concepts[i:i + 3]):
            n = concept_counts.get(cpt["name"], 0)
            badge = (f"<span style='background:#A78BFA33;color:#A78BFA;"
                     f"border-radius:8px;padding:0.05rem 0.45rem;font-size:0.7rem'>"
                     f"反証 {n}件</span>") if n else ""
            c.markdown(
                f"<a href='{cpt['url']}' target='_blank' style='text-decoration:none'>"
                f"<div style='padding:0.7rem;border-radius:12px;background:#161B22;"
                f"border:1px solid #30363D;margin-bottom:0.5rem'>"
                f"<div style='font-weight:700'>{cpt['name']} {badge}</div>"
                f"<div style='font-size:0.85rem;margin-top:0.3rem;color:#D1D5DB'>"
                f"{cpt['line']}</div></div></a>",
                unsafe_allow_html=True)
    st.caption("反証◯件 = その概念の新しい定義を裏打ちする体験の数(反証体験ログの対応概念タグより)。"
               "カードをタップすると詳細ページへ")


PAGES = {"今日": render_today, "コンディション": render_condition,
         "目標": render_goals, "習慣": render_habits, "成長": render_growth,
         "羅針盤": render_compass}
PAGES[page]()

_sync_all = alltime.get("_synced_at")
footer = "データ: Notion API / 自動リロード5分"
if _sync:
    footer += f" / 直近データ同期 {_sync.strftime('%m/%d %H:%M')}"
if _sync_all:
    footer += f" / 全期間データ同期 {_sync_all.strftime('%m/%d %H:%M')}(1時間毎)"
st.caption(footer)
