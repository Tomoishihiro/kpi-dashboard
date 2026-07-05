# 壁掛けKPIダッシュボード セットアップ手順

NotionのKPI(コンディション信号・100km進捗・習慣・7日トレンド)をStreamlitで常時表示するMVP。

## 1. Notionインテグレーションの作成 (5分)

1. https://www.notion.so/profile/integrations を開き「新しいインテグレーション」を作成
   - 名前: 例 `kpi-dashboard`
   - 種類: 内部 (Internal)
   - 権限: **コンテンツを読み取る** のみでOK(書き込み不要)
2. 発行された「内部インテグレーションシークレット」(`ntn_...`)を控える
3. 以下の3ページ/DBそれぞれで、右上「…」→「接続」→ 作成したインテグレーションを追加
   - 💆‍♂️ コンディション記録
   - 🏃 ランニング記録
   - 日次ログ

※ データソースIDは `notion_api.py` に確定値を記載済み。DBを作り直さない限り変更不要。

## 2. ローカル起動 (動作確認)

```bash
cd kpi-dashboard
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# secrets.toml にトークンを記入してから:
streamlit run app.py
```

http://localhost:8501 で表示されれば成功。

## 3. Streamlit Community Cloud へデプロイ

1. このフォルダを **プライベート** GitHubリポジトリへpush
   (`.streamlit/secrets.toml` は `.gitignore` 済みなのでコミットされない)
2. https://share.streamlit.io → New app → リポジトリと `app.py` を指定
3. App settings → **Secrets** に以下を貼り付け:
   ```toml
   NOTION_TOKEN = "ntn_..."
   ```
4. App settings → Sharing → 「Only specific people can view」で自分のメールのみ許可
   (Notionデータが載るので必ず閲覧制限をかける)

## 4. タブレットで常時表示

- Android: **Fully Kiosk Browser** を導入し、Start URLにアプリのURLを設定
  - Motion detection / スケジュール減光で夜間の眩しさと画面焼けを対策
  - アプリ側で5分ごとに自動リロードするため、キオスク側の設定は不要
- 初回アクセス時にStreamlitのログインが必要なため、一度タブレットのブラウザでログインしておく

## 構成

```
app.py                  # ダッシュボード本体(4パネル)
notion_api.py           # Notion APIクライアント(データソースID確定済み)
requirements.txt
.streamlit/config.toml  # ダークテーマ
.streamlit/secrets.toml # トークン(ローカルのみ・Git管理外)
```

## カスタマイズの起点

- 表示期間: `notion_api.fetch_all(days=30)` と `app.py` 内の7日/14日フィルタ
- 100km目標: `app.py` の `GOAL_KM / GOAL_START / GOAL_END`
- キャッシュ/リロード間隔: `@st.cache_data(ttl=300)` と `st_autorefresh(interval=...)`
- パネル追加候補: Zスコア外れ値フラグ、食事記録(PFC)、瞑想ストリーク
