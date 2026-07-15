# 量价数据

Tushare A 股未复权日线量价数据，包含原始 JSON、知识库 Markdown、watermark 和 IMA 上传状态。

```powershell
cd C:\Users\Admin\Documents\股票知识库\量价数据
$env:TUSHARE_TOKEN = '<your-token>'
python src/market_trading/tushare_stockdaily/tushare_stockdaily.py `
  --universe 600519.SH 000001.SZ `
  --today-date 2026-07-14
Remove-Item Env:TUSHARE_TOKEN
```

运行测试：

```powershell
python -m unittest discover -s tests -v
```

IMA 单独重试入口：`python src/upload_ima.py`。相关环境变量仍为 `IMA_UPLOAD_ENABLED`、
`IMA_OPENAPI_CLIENT_ID`、`IMA_OPENAPI_API_KEY`、`IMA_KNOWLEDGE_BASE_ID` 或
`IMA_KNOWLEDGE_BASE_NAME`、`IMA_FOLDER_MAP_JSON`。

默认上传会调用 IMA `create_folder`，自动镜像本地目录：

```text
tushare_stockdaily/<股票代码>/<YYYY-MM-DD>/<原文件名>.md
```

上传状态同时保存远端文件夹 ID，后续运行会复用已有目录，不会重复创建。`IMA_FOLDER_MAP_JSON`
仅用于将某个本地路径强制关联到一个已有的 IMA 文件夹。
