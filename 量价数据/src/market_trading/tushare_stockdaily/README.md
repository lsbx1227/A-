# tushare_stockdaily

Tushare Pro A 股日线行情（`daily` 接口）— 交易日盘后更新的未复权开高低收、成交量和成交额。

- natural_key: `ts_code + trade_date`
- DOC_PREFIX: `px`
- Credentials: `TUSHARE_TOKEN` env var
- partition: `ts_code/date`
- raw format: Tushare `fields/items` 单行映射后的 JSON，并记录首次抓取时间
- endpoint: `https://api.tushare.pro`
- date input: `today_date`，对应 Tushare `trade_date`，每次任务抓取指定交易日

Token 只从环境变量读取，不写入配置、日志、原始数据或处理结果。

当 `IMA_UPLOAD_ENABLED=true` 时，程序在生成 Markdown 和更新抓取水位后，自动扫描并上传尚未同步的 processed 文件。IMA 凭据和知识库 ID 均从环境变量读取。
