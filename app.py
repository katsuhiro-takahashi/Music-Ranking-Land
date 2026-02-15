import os
import requests
from bs4 import BeautifulSoup
from google import genai
from datetime import datetime, timedelta, timezone

# --- 1. 接続設定 ---
# 公開時はこれだけにする
API_KEY = os.getenv("GEMINI_API_KEY") 

if not API_KEY:
    raise ValueError("APIキーが設定されていません。GitHubのSecretsを確認してください。")

client = genai.Client(
    api_key=API_KEY,
    http_options={'api_version': 'v1'}
)

# --- 設定：重み付けと取得数 ---
WEIGHT_YT = 1.0
WEIGHT_SP = 0.8
WEIGHT_IT = 0.5
RANK_LIMIT = 50
ARCHIVE_DIR = "archives"

# タイムゾーンの設定（日本時間 UTC+9）
JST = timezone(timedelta(hours=+9), 'JST')

def get_kworb_data(url, name):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, "html.parser")
        rows = soup.find_all("tr")[1:RANK_LIMIT+1]
        songs = []
        for i, row in enumerate(rows):
            tds = row.find_all("td")
            if len(tds) > 5:
                # タイトルの取得（iTunesのみ列が異なる仕様を維持）
                title = tds[1].text.strip() if name == "iTunes" else tds[2].text.strip()
                
                # --- Pk列の解析ロジック ---
                pk_raw = tds[4].text.strip()  # 例: "1 (x20)" または "1"
                pk_raw += tds[5].text.strip()
                print(pk_raw)
                if "(" in pk_raw:
                    # "(" で分割して、前後を取得
                    parts = pk_raw.split("(")
                    pk_val = parts[0].strip()        # "1"
                    weeks_val = parts[1].replace(")", "").strip() # "x20"
                else:
                    # カッコがない場合は、滞在数は不明（1回とするなど）
                    pk_val = pk_raw
                    weeks_val = "-" 
                
                # 変数名・キー名は変更不可のルールを遵守
                songs.append({
                    "rank": i + 1, 
                    "title": title, 
                    "pk": pk_val,      # 分割した純粋な順位
                    "weeks": weeks_val # 分割した (x20) の部分
                })
        return songs
    except: 
        return []

def generate_sidebar_html(path_prefix):
    """共通のサイドバーHTMLを生成"""
    return f"""
    <aside class='sidebar'>
        <div class='sidebar-box'>
            <h3>MENU</h3>
            <ul>
                <li><a href='{path_prefix}/index.html'>HOME (最新)</a></li>
                <li><a href='{path_prefix}/archive.html'>ARCHIVE (過去一覧)</a></li>
            </ul>
        </div>
        <div class='sidebar-box'>
            <h3>ABOUT</h3>
            <p>Noizzer & Glintが贈る、音楽集計地。<br>独自アルゴリズムが真実を暴く。</p>
        </div>
        <div class='sidebar-box ad-area'>
            <p style='color:#666; font-size:10px;'>ADVERTISEMENT</p>
            <div style='min-height:250px; background:#222; border:1px dashed #444;'></div>
        </div>
    </aside>
    """

def create_archive_page():
    """過去ログ一覧(archive.html)を自動生成"""
    if not os.path.exists(ARCHIVE_DIR): return
    
    files = sorted([f for f in os.listdir(ARCHIVE_DIR) if f.endswith(".html")], reverse=True)
    links_html = "<h1>RANKING ARCHIVE</h1><ul class='archive-list'>"
    
    for f in files:
        # 日付を整形 (20260214_1600_index.html -> 2026/02/14)
        date_str = f"{f[0:4]}/{f[4:6]}/{f[6:8]}_{f[9:13]}"
        links_html += f"<li><a href='archives/{f}'>{date_str} 配信分</a></li>"
    links_html += "</ul>"
    
    with open("archive.html", "w", encoding="utf-8") as f:
        f.write(generate_full_html(links_html, is_in_archive=False))

def get_previous_rank():
    """archivesフォルダから一番新しい過去ログを読み、曲名:順位の辞書を返す"""
    import glob
    files = sorted(glob.glob("archives/*.html"), reverse=True)
    if not files: return {}
    
    with open(files[0], "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")
        prev_ranks = {}
        # index.html内のランキング表示から曲名と順位を抽出
        items = soup.find_all("div", class_="rank-item")
        for item in items:
            try:
                rank = int(item.find("span", class_="num").text)
                title = item.text.split("Peak:")[0].replace(str(rank), "").strip()
                prev_ranks[title] = rank
            except: continue
    return prev_ranks

def generate_talk(final_ranking):
    # 前回の情報を取得
    prev_ranks = get_previous_rank()
    insights = []
    
    for i, (title, score) in enumerate(final_ranking[:10]):
        rank = i + 1
        prev = prev_ranks.get(title)
        if prev:
            diff = prev - rank
            if diff > 5: insights.append(f"{title}: {prev}位から{rank}位へ急上昇！")
            elif diff < -5: insights.append(f"{title}: {prev}位から{rank}位へ急落...")
        else:
            insights.append(f"{title}: 初登場（NEW）！")
    # AIに渡す情報を上位5件＋最下位だけに絞る（節約と精度の向上）
    top_summary = "\n".join([f"{i+1}位:{item[0]} (スコア:{item[1]:.1f})" for i, item in enumerate(final_ranking[:5])])
    bottom_song = f"最下位:{final_ranking[-1][0]}"

    prompt = f"""
    あなたは音楽サイトの掛け合い漫才コンビです。
    以下のランキングデータを見て、音楽ファンがニヤリとするような【300から500文字程度の短い会話】を日本語で作ってください。

    【キャラ設定】
    Noizzer: 毒舌、パンク好き、売れてる曲に厳しい、斜に構えた言い方。
    Glint: 丁寧、最新トレンドに詳しい、Noizzerをなだめる役。

    ミッション】
    1. 今週の1位「{final_ranking[0][0]}」、今週の2位「{final_ranking[1][0]}」、今週の3位「{final_ranking[2][0]}」についてSNSや音楽レビューサイトで囁かれている「リアルな批判」や「ひねくれた意見」をリサーチ（シミュレート）してください。
    2. Noizzerはその意見を引用しつつ、『どこかの誰かが「〇〇」なんて抜かしてたが、全くだぜ』という風に、同調して毒を吐いてください。
    3. Glintはそれに対し、ファン側のポジティブな意見を引用してバランスをとってください。
    4. それからこの変動（{insights}）を見て中身が入っていたら、『めんどくせーが急に人気がでたやつらがいるんだってよ』という風に、特に急上昇した曲をNoizzerがイジってください。空の場合はスルーしてください。
    5. Glintはこの変動（{insights}）を見て中身が入っていたら、『本当は応援しているでしょ』という感じで、Noizzerのイジりをポジティブに応援してバランスをとってください。空の場合はスルーしてください。
    
        【ランキングデータ】
    {top_summary}
    {bottom_song}

    【制約ルール】
    1. 配列名や変数名(final_ranking等)は絶対に使わない。
    2. 1位の「{final_ranking[0][0]}」、2位の「{final_ranking[1][0]}」、3位の「{final_ranking[2][0]}」について、Noizzerに一言毒を吐かせる。
    3. 全体の読み上げは不要。注目曲だけに触れる。
    4. 最後にGlintが締める。
    5. NoizzerとGlintの発言を区別するために<div class='noizzers-talk'>Noizzerのイジり</div><div class='glints-talk'>Glintの締めの言葉</div>という形式で出力する
    6. 発言者の名前を太字にするために<b>名前</b>という形式で出力する事、区切りは <br> を使う。
    """

    response = client.models.generate_content(
        model="gemini-2.5-flash", # 最新の安定版を指定
        contents=prompt
    )

    return f"<div class='ai-talk-box'>{response.text}</div>"

def generate_full_html(main_content, is_in_archive=False):
    path_prefix = ".." if is_in_archive else "."
    sidebar_html = generate_sidebar_html(path_prefix)
    
    return f"""
    <!DOCTYPE html>
    <html lang="ja">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Music Ranking Land</title>
        <link rel="stylesheet" href="{path_prefix}/style.css">
        <!-- Google tag (gtag.js) -->
        <script async src="https://www.googletagmanager.com/gtag/js?id=G-P21011VCNS"></script>
        <script>
            window.dataLayer = window.dataLayer || [];
            function gtag(){dataLayer.push(arguments);}
            gtag('js', new Date());

            gtag('config', 'G-P21011VCNS');
        </script>
    </head>
    <body>
        <div class='container'>
            <main>
                {main_content}
            </main>
            {sidebar_html}
        </div>
    </body>
    </html>
    """


def create_site():
    print("--- Running Noizzer Algorithm ---")
    data_yt = get_kworb_data("https://kworb.net/youtube/insights/jp.html", "YouTube")
    data_sp = get_kworb_data("https://kworb.net/spotify/country/jp_daily.html", "Spotify")
    data_it = get_kworb_data("https://kworb.net/popjp/", "iTunes")
    
    # --- ポイント集計ロジック ---
    scores = {}
    song_info = {}
    for data, weight in [(data_yt, WEIGHT_YT), (data_sp, WEIGHT_SP), (data_it, WEIGHT_IT)]:
        for s in data:
            title = s['title']
            scores[title] = scores.get(title, 0) + (RANK_LIMIT + 1 - s['rank']) * weight
            if title not in song_info: song_info[title] = s
            
    final_ranking = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:RANK_LIMIT]
    now = datetime.now(JST).strftime('%Y-%m-%d %H:%M')
    
    # --- メインコンテンツ生成 ---
    main_html = f"<h1>MUSIC RANKING LAND</h1><p class='date'>Published: {now}</p>"
    main_html += "<div class='talk'><strong>Noizzer:</strong> 「今週のランキングだ。見ろ、数字は嘘をつかねえ。」<br><strong>Glint:</strong> 「はい。興味深い動きですね。」</div>"
    main_html += "<div class='main-ranking'>"
    for i, (title, score) in enumerate(final_ranking):
        info = song_info.get(title, {"pk":"-", "weeks":"-"})
        main_html += f"<div class='rank-item'><div class='num'>{i+1}</div><div class='song-detail'> {title} <div class='meta'>Peak:{info['pk']} / Weeks:{info['weeks']}</div></div></div>"
    main_html += "</div>"

    # NoizzerとGlintのやり取り
    main_html += generate_talk(final_ranking)

   # 3. Data Evidence
    main_html += "<h3>RAW DATA EVIDENCE</h3>"
    main_html += "<div class='grid'>"
    for plat, songs in [("YouTube", data_yt), ("Spotify", data_sp), ("iTunes", data_it)]:
        main_html += f"<div class='col'><h4>{plat}</h4><table>"
        main_html += "<tr><th class='col-rank'>#</th><th class='col-title'>Title</th><th class='col-pk'>Peak</th><th class='col-weeks'>Weeks</th></tr>"
        for s in songs:
            main_html += f"<tr><td>{s['rank']}</td><td class='col-title'>{s['title']}</td><td class='col-pk'>{s['pk']}</td><td class='col-weeks'>{s['weeks']}</td></tr>"
        main_html += "</table></div>"
    main_html += "</div>"

    main_html += f"<p style='text-align:center; font-size:0.8em; margin-top:40px;'>Updated: {now} | Data: Kworb.net</p>"

    # --- ファイル出力 ---
    # 1. index.html
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(generate_full_html(main_html, is_in_archive=False))
    
    # アーカイブ用コピー保存
    if not os.path.exists(ARCHIVE_DIR): os.makedirs(ARCHIVE_DIR)
    ts = datetime.now(JST).strftime('%Y%m%d_%H%M')
    with open(f"{ARCHIVE_DIR}/{ts}_index.html", "w", encoding="utf-8") as f:
        f.write(generate_full_html(main_html, is_in_archive=True))
    
    # アーカイブ一覧ページも更新
    create_archive_page()
    print("--- Deployment Complete (index.html & archive.html) ---")

if __name__ == "__main__":
    create_site()