<p align="center">
  <img src="../../.github/assets/logo.png" width="120" alt="TalyxTerminal 标志">
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
  <b>简体中文</b> ·
  <a href="README.zh-TW.md">繁體中文</a> ·
  <a href="README.ja.md">日本語</a> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.es.md">Español</a> ·
  <a href="README.ru.md">Русский</a>
</p>

**一个跑在你自己电脑上、从里到外都开放的金融终端。**

图表、研究，还有替你盯盘的 AI，都在同一个工作区里。数据留在你的电脑上，key 握在你自己手里。免费开源。

<p align="center"><img src="../../.github/assets/screenshot-chart-agent.png" alt="Chart 与 Agent 并排"></p>

## 生来开放

TalyxTerminal 不是一个大而全的应用，而是一层很薄的平台加上一组模块。

- **官方模块没有特权。** 模块之间只通过各自公开的东西配合，官方模块和第三方模块用的是同一套契约。我们能做的，你也能做。
- **哪里都能扩展。** 接入一个数据源，Chart、Quant 和 Agent 立刻都能用；写一个指标，图表、警报和筛选都认得它。
- **人和 AI 用同一套动作。** 你在界面上能做的事，AI 也能做；外部 AI 工具可以通过 MCP 接进来，在同一个工作区里干活。
- **Community** 是模块的家：有哪些模块、装了哪些、在哪里打开。

<p align="center"><img src="../../.github/assets/screenshot-community.png" alt="Community"></p>

设计细节见 [docs/architecture.md](../architecture.md)（英文）。

## 官方模块

**[Chart](../../modules/chart/README.md)：看图和盯盘。** TradingView 风格的图表，指标用 Python 写。
- 几十个内置指标，十几种图型（含平均 K 线、砖形图、卡吉图、点数图），一个工作区最多八张联动的图
- 指标就是 Python：Pine 能表达的都能写，还能用 scikit-learn、PyTorch 这类库，把机器学习模型直接画到图上
- 价格、指标和画线警报；K 线回放；按指标条件筛选一组品种

**[Quant](../../modules/quant/README.md)：量化研究和回测。** 从一个想法走到一个信得过的策略，全程都在这里。
- 因子：写一行公式，几秒内看到 IC、分组收益和换手
- 模型：用机器学习把因子组合成信号，按滚动前推的方式训练和检验
- 回测用 Nautilus 引擎，计入手续费、价差、冲击、融券和资金费率；结果是一份完整报告：净值和回撤、危机时段、风格归因、成本和容量
- 组合配置、笔记本，以及一个资料库：发布研究结果并持续更新，供策略使用

<p align="center"><img src="../../.github/assets/screenshot-quant-results.png" alt="Quant 策略结果"></p>

**[Agent](../../modules/agent/README.md)：懂你工作区的 AI。** 接入你选的模型，它用的工具和你一样。
- 让它取数据、建图、写指标、跑回测，它的改动可以逐轮撤回
- Bot 有自己的职责、预算和时间表：开盘前写好简报，警报响了就醒来处理，也可以几个一起协作
- 动手之前按你设定的权限征求同意，并记下每个模型、每次运行花了多少钱

<p align="center"><img src="../../.github/assets/screenshot-bot.png" alt="正在工作的 Bot"></p>

**[Market](../../modules/market/README.md)：所有行情数据的地基。** 别的模块都向 Market 要数据；数据从哪来、怎么存，是 Market 的事。
- 美股、A 股、港股、加密货币和指数，开箱即用的免费数据源
- 读数据先读本机，存过的数据离线也能用
- 填上付费数据源的 key，Market 就换用更好的来源，别的什么都不用改

**[Developer](../../modules/developer/README.md)：写模块和指标的地方。** 代码、文档和示例放在一起，可以在 VS Code 里打断点调试。

## 使用指南

[中文版](../user-guide.zh-CN.md)和[英文版](../user-guide.md)介绍安装、工作区、各模块的用法及数据存放位置。

## 快速开始

从 [Releases](https://github.com/talyxterminal/TalyxTerminal/releases) 下载安装包运行即可。也可以从源码运行：

```powershell
uv sync --python 3.13 --locked
npm --prefix web ci
.\.venv\Scripts\python.exe -m host.desktop
```

目前支持 Windows x64。完整的安装步骤（包括 Quant 的运行件）见 [docs/development.md](../development.md)（英文）。

## 数据从哪里来

TalyxTerminal 在你的电脑上直接向数据源取数据，不经过我们的服务器。取过一次的数据存在本机，之后从本机读。

**加密货币**来自各交易所的官方 API 和历史文件。分钟线、资金费率、持仓量、多空比和主动买卖量都有完整历史，指标可以直接读。各交易所能追溯多久不一样，少数交易所在某些地区连不上。

**用股票数据做回测时，要留意这几件事。**

- **美股。** 纳斯达克的免费名单只列现在还在交易的股票。拿这份名单回测过去，会漏掉后来退市的公司。填入付费的 Sharadar 密钥，可以补上它有价格记录的退市美股。
- **A 股。** 免费的 Baostock 数据包含退市股票，也能查到沪深 300、中证 500 和上证 50 每周有哪些成分股。回测用的是股票当时的行业分类。TALYX 每季度查一次，所以公司换了行业，最晚要到下个季度才会体现。
- **行业和做空。** 美股和港股回测仍沿用今天的行业分类。免费数据也查不到某天哪只股票能借到，所以回测做空时，默认想借的股票都借得到。

每个数据源都有自己的使用条款，见 [docs/limits.md](../limits.md)（英文）。

## 路线图

Trade（交易与实盘执行）· Intel（新闻与事件）· Community 模块市场

## 支持这个项目

如果 TalyxTerminal 对你有用，欢迎请作者喝杯咖啡。

<table>
  <tr>
    <td align="center"><img src="../../.github/assets/donate-alipay.png" width="220" alt="支付宝"><br>支付宝</td>
    <td align="center"><img src="../../.github/assets/donate-usdt-trc20.png" width="220" alt="USDT (TRC20)"><br>USDT · <b>仅限 TRC20</b><br><code>TJJGEou3agCdKMhvACfWc87ALykkTxtcRx</code></td>
  </tr>
</table>

USDT 只能走 TRC20（波场）网络。从其他网络转入无法找回。

## 许可与致谢

TalyxTerminal 以 [Apache-2.0](../../LICENSE) 许可发布。它建立在许多开源项目之上，包括 TradingView Lightweight Charts、NautilusTrader、Qlib 和 PyneCore，详见 [NOTICE](../../NOTICE)。

TalyxTerminal 不构成投资建议。
