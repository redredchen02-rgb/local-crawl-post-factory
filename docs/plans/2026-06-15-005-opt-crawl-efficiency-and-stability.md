# Crawl 效率与稳定性优化计划

## 概览

优化 `<button hx-post="/crawl">` 触发的整个「爬取 + 建包」管线。两块独立优化互不阻塞：
爬虫引擎稳定性（Scrapy 设置）和封面下载并行化（管线编排）。

安全边界不变、CLI 契约不变、R6 发布模型不变。

---

## 优化 1：爬虫引擎稳定性（Scrapy 设置）

### 现状

`src/crawl_posts.py` 的 `_Spider.custom_settings` 当前:

```python
custom_settings = {
    "RETRY_ENABLED": False,
    "DOWNLOAD_TIMEOUT": 30,
    "CONCURRENT_REQUESTS": 8,
    "DOWNLOAD_DELAY": 0.0,
    # No AutoThrottle
}
```

没有重试、没有自动限速、超时只有一个固定值。对暂时性网络抖动（DNS 抖动、服务器
503/502）零容错，爬一般直接失败。

### 变更

#### 1a: 启用 Scrapy Retry Middleware

```python
"RETRY_ENABLED": True,
"RETRY_TIMES": 2,                     # 最多重试 2 次
"RETRY_HTTP_CODES": [502, 503, 504, 408, 429],  # 只重试暂时性服务端错误
```

- 不重试 404/403/401——那些不是暂时性的
- `RETRY_TIMES=2` 意味着一共最多 3 次尝试
- Scrapy RetryMiddleware 自带指数退避（约 0.5s/1s/2s）

#### 1b: 启用 Scrapy AutoThrottle Extension

```python
"AUTOTHROTTLE_ENABLED": True,
"AUTOTHROTTLE_START_DELAY": 0.5,      # 初始延迟: 从保守开始
"AUTOTHROTTLE_MAX_DELAY": 5.0,        # 最多延迟 5 秒
"AUTOTHROTTLE_TARGET_CONCURRENCY": 4, # 目标并发: 比初始 8 更保守
```

- AutoThrottle 根据服务器响应时间动态调整延迟和并发
- 快速站点自动加速，慢速站点自动减速
- 对于目标站点（如 51cg1.com）友好
- 保留了用户设定的 `DOWNLOAD_DELAY` 作为基准（若设置 > 0）

#### 1c: 添加 DNS / 连接保护

```python
"DNSCACHE_ENABLED": True,              # 启用 DNS 缓存（默认已开，显式声明）
"DOWNLOAD_MAXSIZE": 5 * 1024 * 1024,  # 拒绝超过 5MB 的响应（防止 OOM）
"DOWNLOAD_WARNSIZE": 0,                # 不打印大响应警告
```

- `DOWNLOAD_TIMEOUT` 保留现有的 30s
- 若用户通过 webui.yaml 设置了 `download_delay` > 0，同时保留
- AutoThrottle 在 `DOWNLOAD_DELAY` 之上工作（取最大值）

### 涉及文件

- `src/crawl_posts.py` —— `custom_settings` 字典
- `configs/crawler.yaml` —— 如有需要新增的默认配置

### 不做

- 不改 `CLOSESPIDER_PAGECOUNT` / `CONCURRENT_REQUESTS` 默认值（8 个并发适合大多数场景）
- 不改 `ROBOTSTXT_OBEY` 逻辑（当前默认 `not opts["no_robots"]`，已有 CLI 控制）
- 不改 `COOKIES_ENABLED`（当前 False，爬自有站点不需要 cookies）

---

## 优化 2：封面下载并行化

### 现状

`core/pipeline.py` 第 95-118 行在一个 `for rec in deduped:` 循环里逐一处理每篇
文章——对每篇依次执行 caption → cover download → watermark → build。其中封面下载
(`select_cover.select()`) 是网络 I/O 操作，一次只下载一张图片。

当有 50 篇内容、每张封面 200KB-1MB 时，纯串行下载导致管线大部分时间在等待网络。

### 变更

将封面下载从串行循环中提取出来，改为**批量并行下载**：

#### 2a: 新增 `download_all_covers()` 函数

放在 `core/pipeline.py` 中（或 `src/select_cover.py` 中新增一个批量函数），功能：

```python
def download_all_covers(
    records: list[dict],
    download_dir: Path,
    timeout: int,
    retries: int,
    backoff_sec: float,
    max_workers: int = 5,
) -> list[dict]:
    """Parallel batch download covers for all records. Returns updated records.
    
    Uses ThreadPoolExecutor (max_workers=5) for concurrent I/O-bound downloads.
    Each download is independent: one failure never aborts others.
    Failed downloads are recorded in the record's cover_error field.
    """
```

#### 2b: 修改 `run_pipeline()` 流程

流水线变成：

```
Stage 1: normalize           (逐项)
Stage 2: dedupe              (批处理)
Stage 3: caption             (逐项)
Stage 4: **batch cover download**  (并行批处理 —— 新增)
Stage 5: watermark + build   (逐项)
```

具体变更 `core/pipeline.py`：

1. 在所有 caption 渲染完成后（`for rec in deduped` 之前），收集所有需下载的 `image_url` 记录
2. 调用 `download_all_covers()` 一次性并行下载
3. 返回更新后的 records（含 `cover_source`/`cover_path`）
4. 后续的 watermark/build 循环直接使用已下载的本地文件

#### 2c: 失败处理

- 每张封面下载独立隔离
- 下载失败 → 记录在 `cover_error` 字段（而不是抛异常）
- Pipeline 后续步骤感知 `cover_path` 为 None → 跳过 watermark，正常 build
- 失败的 cover 显示在 `failed` 列表中（`stage=cover`）

### 涉及文件

- `core/pipeline.py` —— 新增 `download_all_covers()` + 修改 `run_pipeline()` 流程
- `src/select_cover.py` —— 如有共用下载函数被抽取（当前已有 `_fetch` 可复用）
- `configs/webui.yaml` —— 新增可选 `cover_download_concurrency: 5`
- `core/webui_config.py` —— 新增 `cover_download_concurrency` 默认值

### 不做

- 不引入 aiohttp / httpx 等新依赖——直接复用 `select_cover._fetch`（基于 `urllib`）
- 不改成异步架构——`ThreadPoolExecutor` 足够应对 I/O-bound 封面下载
- 不做封面缓存优化——`_fetch` 已有 "不覆盖已存在文件"（R4），自动复用

---

## 优化 3：配置与 WebUI 设定页

### 3a: 新增优化相关的配置字段

`core/webui_config.py` DEFAULTS 增加：
```python
"cover_download_concurrency": 5,
```

`configs/webui.yaml` 可选添加：
```yaml
cover_download_concurrency: 5
```

`core/webui_config.py` 的 `_INT_FIELDS` 加上 `"cover_download_concurrency"`。

### 3b: WebUI 设定页

不变——不对 WebUI 表单做变动，默认值 5 适用于多数场景。
用户如需调整可手改 `configs/webui.yaml`。

---

## 测试计划

### T1: 爬虫重试配置

- `tests/test_crawl_posts.py` 新增测试：验证 `custom_settings` 包含正确的 retry 和
  AutoThrottle 配置
- 方法：检查 `_Spider` 编译后的 `custom_settings` 字典（通过 SPI 反射或断言子进程配置）

### T2: 并行封面下载

- `tests/test_pipeline.py` 新增测试：传入多个有 `image_url` 的 item，验证并行下载是否完成
- 使用猴补丁（monkeypatch）替换 `select_cover._fetch` 为一个带短延迟的 fake 来验证并发行为
- 测试部分失败：部分图片下载失败不影响其他图片

### T3: 回归

- `tests/test_pipeline.py` 现有 9 个测试必须全绿
- `tests/test_crawl_posts.py` 现有 4 个测试必须全绿
- `tests/test_webui_crawl.py` 现有 3 个测试必须全绿

---

## 执行顺序 & 依赖

```
Wave 1（无依赖）:
  ├── 1a: src/crawl_posts.py — RETRY + AutoThrottle + DOWNLOAD_MAXSIZE 设置
  ├── 3a: core/webui_config.py — 新增 cover_download_concurrency 配置
  └── T1: tests/test_crawl_posts.py — 配置验证测试

Wave 2（依赖 3a）:
  ├── 2a/2b: core/pipeline.py + src/select_cover.py — 并行封面下载
  └── T2: tests/test_pipeline.py — 并行下载测试

Wave 3（回归）:
  └── 全量测试验证
```

---

## 不做

- ❌ 增量爬取 / 爬虫状态持久化（增加复杂性，对一次性批量爬取收益不大）
- ❌ Scrapy 替换为 requests/aiohttp（重写一个能跑的生产级爬虫 > 收益）
- ❌ WebUI 表单改动（配置默认值适用，手动调整 yaml 即可）
- ❌ 修改 CLI 接口 / 退出码契约
- ❌ 修改发布闸门 / R6 publish 流程
