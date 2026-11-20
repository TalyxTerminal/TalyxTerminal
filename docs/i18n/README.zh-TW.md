<p align="center">
  <img src="../../.github/assets/logo.png" width="120" alt="TalyxTerminal 標誌">
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
  <b>繁體中文</b> ·
  <a href="README.ja.md">日本語</a> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.es.md">Español</a> ·
  <a href="README.ru.md">Русский</a>
</p>

**一個跑在你自己電腦上、從裡到外都開放的金融終端。**

圖表、研究，還有替你盯盤的 AI，都在同一個工作區裡。資料留在你的電腦上，金鑰握在你自己手裡。免費開源。

<p align="center"><img src="../../.github/assets/screenshot-chart-agent.png" alt="Chart 與 Agent 並排"></p>

## 生來開放

TalyxTerminal 不是一個大而全的應用程式，而是一層很薄的平台加上一組模組。

- **官方模組沒有特權。** 模組之間只透過各自公開的東西配合，官方模組和第三方模組用的是同一套契約。我們能做的，你也能做。
- **哪裡都能擴充。** 接入一個資料來源，Chart、Quant 和 Agent 立刻都能用；寫一個指標，圖表、警示和篩選都認得它。
- **人和 AI 用同一套動作。** 你在介面上能做的事，AI 也能做；外部 AI 工具可以透過 MCP 接進來，在同一個工作區裡工作。
- **Community** 是模組的家：有哪些模組、裝了哪些、在哪裡開啟。

<p align="center"><img src="../../.github/assets/screenshot-community.png" alt="Community"></p>

設計細節見 [docs/architecture.md](../architecture.md)（英文）。

## 官方模組

**[Chart](../../modules/chart/README.md)：看圖與盯盤。** TradingView 風格的圖表，指標用 Python 寫。
- 數十個內建指標，十幾種圖型（含平均 K 線、磚形圖、卡吉圖、點數圖），一個工作區最多八張連動的圖
- 指標就是 Python：Pine 能表達的都能寫，還能用 scikit-learn、PyTorch 這類函式庫，把機器學習模型直接畫到圖上
- 價格、指標與畫線警示；K 線回放；依指標條件篩選一組商品

**[Quant](../../modules/quant/README.md)：量化研究與回測。** 從一個想法走到一個信得過的策略，全程都在這裡。
- 因子：寫一行公式，幾秒內看到 IC、分組報酬與週轉率
- 模型：用機器學習把因子組合成訊號，以滾動前推的方式訓練與檢驗
- 回測使用 Nautilus 引擎，計入手續費、價差、衝擊、借券與資金費率；結果是一份完整報告：淨值與回撤、危機時段、風格歸因、成本與容量
- 投資組合配置、筆記本，以及一個資料庫：發布研究結果並持續更新，供策略使用

<p align="center"><img src="../../.github/assets/screenshot-quant-results.png" alt="Quant 策略結果"></p>

**[Agent](../../modules/agent/README.md)：懂你工作區的 AI。** 接入你選的模型，它用的工具和你一樣。
- 讓它取資料、建圖、寫指標、跑回測，它的變更可以逐輪撤回
- Bot 有自己的職責、預算與時間表：開盤前寫好簡報，警示響了就醒來處理，也可以幾個一起協作
- 動手之前依你設定的權限徵求同意，並記下每個模型、每次執行花了多少錢

<p align="center"><img src="../../.github/assets/screenshot-bot.png" alt="正在工作的 Bot"></p>

**[Market](../../modules/market/README.md)：所有行情資料的地基。** 其他模組都向 Market 要資料；資料從哪來、怎麼存，是 Market 的事。
- 美股、A 股、港股、加密貨幣與指數，開箱即用的免費資料來源
- 讀資料先讀本機，存過的資料離線也能用
- 填上付費資料來源的金鑰，Market 就換用更好的來源，其他什麼都不用改

**[Developer](../../modules/developer/README.md)：寫模組與指標的地方。** 程式碼、文件與範例放在一起，可以在 VS Code 裡設中斷點除錯。

## 快速開始

從 [Releases](https://github.com/talyxterminal/TalyxTerminal/releases) 下載安裝程式執行即可。也可以從原始碼執行：

```powershell
uv sync --python 3.13 --locked
npm --prefix web ci
.\.venv\Scripts\python.exe -m host.desktop
```

目前支援 Windows x64。完整的安裝步驟（包括 Quant 的執行元件）見 [docs/development.md](../development.md)（英文）。

## 資料從哪裡來

TalyxTerminal 在你的電腦上直接向資料來源取資料，不經過我們的伺服器。取過一次的資料存在本機，之後從本機讀取。

**加密貨幣**來自各交易所的官方 API 與歷史檔案。分鐘線、資金費率、未平倉量、多空比與主動買賣量都有完整歷史，指標可以直接讀取。各交易所能回溯多久不一樣，少數交易所在某些地區連不上。

**股票**：美股、A 股與港股的價格、財報、產業與股本來自免費的公開來源。免費的股票資料有三點值得知道：
- 它涵蓋的是今天仍在交易的公司，回測期間下市的公司不在樣本裡；
- 產業分類是今天的，不是當時的；
- 沒有借券資料，放空預設總能借到股票。

做長週期的股票回測時記著這幾點。需要更多時，填上 Sharadar 這類付費來源的金鑰，Market 會自己切換過去。

每個資料來源都有自己的使用條款，見 [docs/limits.md](../limits.md)（英文）。

## 路線圖

Trade（交易與實盤執行）· Intel（新聞與事件）· Community 模組市集

## 支持這個專案

如果 TalyxTerminal 對你有用，歡迎請作者喝杯咖啡。

<table>
  <tr>
    <td align="center"><img src="../../.github/assets/donate-alipay.png" width="220" alt="支付寶"><br>支付寶</td>
    <td align="center"><img src="../../.github/assets/donate-usdt-trc20.png" width="220" alt="USDT (TRC20)"><br>USDT · <b>僅限 TRC20</b><br><code>TJJGEou3agCdKMhvACfWc87ALykkTxtcRx</code></td>
  </tr>
</table>

USDT 只能走 TRC20（波場）網路。從其他網路轉入無法找回。

## 授權與致謝

TalyxTerminal 以 [Apache-2.0](../../LICENSE) 授權發布。它建立在許多開源專案之上，包括 TradingView Lightweight Charts、NautilusTrader、Qlib 與 PyneCore，詳見 [NOTICE](../../NOTICE)。

TalyxTerminal 不構成投資建議。
