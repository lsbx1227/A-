# tushare_stockdaily 数据源说明

本来源通过 Tushare Pro `daily` 接口获取指定交易日的 A 股未复权日线行情。

## 输入

- `universe`：股票代码列表，例如 `600519.SH`、`000001.SZ`
- `today_date`：本次要抓取的交易日，支持 `YYYY-MM-DD` 或 `YYYYMMDD`
- `TUSHARE_TOKEN`：通过环境变量提供，不写入代码、日志或数据文件

## 输出

- Raw：`data/raw/market_trading/tushare_stockdaily/<ts_code>/<YYYY-MM-DD>/`
- Processed：`data/processed/market_trading/tushare_stockdaily/<ts_code>/<YYYY-MM-DD>/`
- Watermark：`data/state/watermarks/market_trading/tushare_stockdaily.json`
- Log：`log/market_trading/tushare_stockdaily/<date>.log`

同一股票和交易日使用稳定 `doc_id`；内容未变化时跳过，发生修订时生成下一版本。

## IMA 自动上传

- `IMA_UPLOAD_ENABLED=true`：启用自动上传
- `IMA_OPENAPI_CLIENT_ID`：IMA OpenAPI Client ID
- `IMA_OPENAPI_API_KEY`：IMA OpenAPI API Key
- `IMA_KNOWLEDGE_BASE_ID`：目标知识库内部 ID，与名称二选一
- `IMA_KNOWLEDGE_BASE_NAME`：目标知识库名称，例如 `股票tushare量价数据`
- `IMA_FOLDER_MAP_JSON`：可选的文件夹映射，例如 `{"600519.SH/2026-07-13":"<folder-id>","600519.SH":"<folder-id>"}`

文件夹映射优先级为“股票/日期 → 股票 → 日期 → `*`”。没有映射时，程序自动调用 `create_folder`，创建并复用 `tushare_stockdaily/股票代码/日期` 目录树，文件保持本地原文件名。
