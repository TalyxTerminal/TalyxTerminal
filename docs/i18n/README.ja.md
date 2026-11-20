<p align="center">
  <img src="../../.github/assets/logo.png" width="120" alt="TalyxTerminal ロゴ">
</p>

<h1 align="center">TalyxTerminal</h1>

<p align="center"><b>Build limitless.</b></p>

<p align="center">
  <a href="../../LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-1f1f1f?style=flat-square" alt="License: Apache-2.0"></a>
  <img src="https://img.shields.io/badge/platform-Windows%20x64-1f1f1f?style=flat-square" alt="Platform: Windows x64">
  <img src="https://img.shields.io/badge/python-3.13-1f1f1f?style=flat-square" alt="Python 3.13">
  <a href="https://github.com/talyxterminal/TalyxTerminal/releases"><img src="https://img.shields.io/github/v/release/talyxterminal/TalyxTerminal?style=flat-square&color=1f1f1f" alt="Latest release"></a>
  <a href="https://github.com/talyxterminal/TalyxTerminal/stargazers"><img src="https://img.shields.io/github/stars/talyxterminal/TalyxTerminal?style=flat-square&color=1f1f1f" alt="Stars"></a>
  <img src="https://img.shields.io/badge/free-open%20source-f36b21?style=flat-square" alt="Free and open source">
</p>

<p align="center">
  <a href="../../README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.zh-TW.md">繁體中文</a> ·
  <b>日本語</b> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.es.md">Español</a> ·
  <a href="README.ru.md">Русский</a>
</p>

**自分のパソコンで動き、隅々までオープンな金融ターミナル。**

チャート、リサーチ、そして相場を見張ってくれる AI が、ひとつのワークスペースに揃っています。データは手元のマシンに、API キーはあなたの手に。無料でオープンソースです。

<p align="center"><img src="../../.github/assets/screenshot-chart-agent.png" alt="Chart と Agent を並べた画面"></p>

## 設計からオープン

TalyxTerminal はひとつの巨大なアプリケーションではありません。薄いプラットフォームと、いくつものモジュールでできています。

- **公式モジュールに特別扱いはありません。** モジュール同士は互いが公開しているものだけを通じて連携し、公式モジュールもサードパーティと同じ契約を使います。私たちが作れるものは、あなたにも作れます。
- **どこからでも拡張できます。** データソースをひとつ追加すれば、Chart も Quant も Agent もすぐに使えます。インジケーターを書けば、チャートもアラートもスクリーニングもそれを理解します。
- **人と AI は同じ操作を使います。** 画面でできることは AI にもでき、外部の AI ツールも MCP で接続して同じワークスペースで作業できます。
- **Community** はモジュールの居場所です。どんなモジュールがあり、何がインストール済みで、どこで開くのかがわかります。

<p align="center"><img src="../../.github/assets/screenshot-community.png" alt="Community"></p>

設計の詳細は [docs/architecture.md](../architecture.md)（英語）にあります。

## 公式モジュール

**[Chart](../../modules/chart/README.md)：チャートと相場の監視。** インジケーターを Python で書ける、TradingView 風のチャート。
- 数十の組み込みインジケーター、平均足・練行足・カギ足・ポイント＆フィギュアを含む十数種のチャートタイプ、ワークスペースあたり最大 8 枚の連動チャート
- インジケーターは Python：Pine で表現できることはすべて書け、scikit-learn や PyTorch などのライブラリも使えるので、機械学習モデルをそのままチャートに描けます
- 価格・インジケーター・描画ツールのアラート、バーリプレイ、インジケーター条件による銘柄スクリーニング

**[Quant](../../modules/quant/README.md)：クオンツリサーチとバックテスト。** アイデアから信頼できる戦略まで、すべての道のりをここで。
- ファクター：1 行の数式を書くだけで、数秒で IC、分位リターン、回転率を確認
- モデル：機械学習でファクターを組み合わせてシグナルにし、ウォークフォワードで学習・検証
- Nautilus エンジンによるバックテスト。手数料、スプレッド、インパクト、貸株料、資金調達コストを反映し、結果は資産推移とドローダウン、危機期間、スタイル要因分析、コスト、キャパシティまで揃った完全なレポート
- ポートフォリオ配分、ノートブック、そしてリサーチ結果を公開し更新し続けて戦略から使えるようにするライブラリ

<p align="center"><img src="../../.github/assets/screenshot-quant-results.png" alt="Quant の戦略結果"></p>

**[Agent](../../modules/agent/README.md)：ワークスペースを理解する AI。** 好きなモデルを接続でき、あなたと同じツールで作業します。
- データの取得、チャートの作成、インジケーターの作成、バックテストの実行を頼めて、変更はターンごとに元に戻せます
- Bot はそれぞれ役割、予算、スケジュールを持ちます。寄り付き前にブリーフを書き、アラートで目を覚まし、グループで協力することもできます
- 行動する前に、あなたが設定した権限に従って確認を求め、モデルごと・実行ごとのコストを記録します

<p align="center"><img src="../../.github/assets/screenshot-bot.png" alt="作業中の Bot"></p>

**[Market](../../modules/market/README.md)：すべての市場データの土台。** 他のモジュールは Market にデータを求めます。データの出どころと保存方法は Market の仕事です。
- 米国株、中国 A 株、香港株、暗号資産、指数。すぐに使える無料のデータソース付き
- 読み込みはまず手元のマシンから。保存済みのデータはオフラインでも使えます
- 有料データソースのキーを追加すれば Market がより良いソースに切り替えます。他は何も変える必要がありません

**[Developer](../../modules/developer/README.md)：モジュールとインジケーターを書く場所。** コード、ドキュメント、サンプルが一か所に。VS Code でブレークポイントデバッグもできます。

## クイックスタート

[Releases](https://github.com/talyxterminal/TalyxTerminal/releases) からインストーラーをダウンロードして実行してください。ソースから動かすこともできます：

```powershell
uv sync --python 3.13 --locked
npm --prefix web ci
.\.venv\Scripts\python.exe -m host.desktop
```

現在は Windows x64 に対応しています。Quant の実行ファイルを含む完全なセットアップは [docs/development.md](../development.md)（英語）にあります。

## データの出どころ

TalyxTerminal はあなたのパソコンから直接データソースにアクセスします。私たちのサーバーを経由することはありません。一度取得したデータは手元に保存され、以後はそこから読み込みます。

**暗号資産**は各取引所の公式 API と履歴ファイルから取得します。分足、資金調達率、建玉、ロング／ショート比率、テイカー出来高は全履歴が揃い、インジケーターから直接読めます。遡れる期間は取引所ごとに異なり、一部の取引所は地域によっては接続できません。

**株式**：米国株、中国 A 株、香港株の価格、財務諸表、業種、株式数は無料の公開ソースから取得します。無料の株式データについて知っておきたい点が 3 つあります：
- 現在取引されている企業が対象なので、バックテスト期間中に上場廃止となった企業はサンプルに含まれません。
- 業種分類は当時のものではなく現在のものです。
- 貸株データがないため、空売りは常に株を借りられる前提です。

長期の株式バックテストではこれらを念頭に置いてください。さらに必要なときは、Sharadar などの有料ソースのキーを追加すれば Market が自動で切り替えます。

各データソースには独自の利用規約があります。[docs/limits.md](../limits.md)（英語）をご覧ください。

## ロードマップ

Trade（売買と実運用）· Intel（ニュースとイベント）· Community モジュールマーケットプレイス

## プロジェクトを支援する

TalyxTerminal が役に立ったら、作者にコーヒーを一杯ごちそうしていただけるとうれしいです。

<table>
  <tr>
    <td align="center"><img src="../../.github/assets/donate-alipay.png" width="220" alt="Alipay"><br>Alipay</td>
    <td align="center"><img src="../../.github/assets/donate-usdt-trc20.png" width="220" alt="USDT (TRC20)"><br>USDT · <b>TRC20 のみ</b><br><code>TJJGEou3agCdKMhvACfWc87ALykkTxtcRx</code></td>
  </tr>
</table>

USDT は TRC20（Tron）ネットワークでのみ送金してください。他のネットワークでの送金は取り戻せません。

## ライセンスと謝辞

TalyxTerminal は [Apache-2.0](../../LICENSE) ライセンスで公開されています。TradingView Lightweight Charts、NautilusTrader、Qlib、PyneCore をはじめ多くのオープンソースプロジェクトの上に成り立っています。詳しくは [NOTICE](../../NOTICE) をご覧ください。

TalyxTerminal は投資助言ではありません。
