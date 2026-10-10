# 高圈复杂度函数拆分手册

> 资料库。仅在检测出某个函数 CCN 明显超标、或用户点名要拆某个函数时读取。
> 核心原则：**重构的价值不在于代码变短，而在于能拿出证据证明行为没变。没有差分测试的纯重构只是"我觉得应该没问题"。**

## 1. 口径与校准

风险表里的"圈复杂度"是**单函数 McCabe CCN**：

```
基础 1，然后每个判定点 +1：
    if / elif（elif 在 AST 里是嵌套 If，自动计入）
    for / async for
    while
    except 处理器
    assert
    comprehension（list / set / dict / generator）
    三元表达式
    布尔运算符的每个额外操作数：a and b and c → +2
else 和 finally 不是判定点。
```

**动手前先跑 `scripts/detect.py --ccn-threshold 20` 自测一遍，和平台数字对。**

实测校准结果（同一份代码、同一版本）：

| 函数 | 平台表 | 自测脚本 | 偏差 |
| --- | --- | --- | --- |
| 入口 `main` | 41 | 41 | 0 |
| 批次执行 `run_batch` | 34 | 35 | +1 |
| 远端部署 `deploy_artifact` | 24 | 24 | 0 |

**结论：脚本口径和平台基本一致，但会有几个点的偏差。** 因此：

- 排序和分档（哪些函数要拆、先拆哪个）是可信的；
- 不要拿自测数字去和平台争"谁更准"——偏差 1~2 点不影响决策；
- 平台数字、自测数字、commit 里写的数字三者不一致时（本次三个口径分别给出 34 / 35 / 37），以"改动前后同口径对比"为准，不追求对齐绝对值。

```bash
python scripts/detect.py --lang python --ccn-threshold 20 path/to/file.py
```

脚本按函数各自计量，嵌套函数单独成行、不计入父函数，因此和平台口径可比。

## 2. 拆分前先确认边界

- **只拆表里点名的函数。** 顺手拆没列入表的函数会扩大 diff、增加回归面，还会让"行为未变"的证据变弱。
- **但表不是全集。** 实测同一版本里自测排名为 `main` 41、`run_one` 35、`run_batch` 35、`deploy_artifact` 24；表中收了后两者，没收 `run_one`。所以入选标准不是 CCN 排序——不要按自己的排名替换名单，但拆完名单内函数后，要主动报告"还有哪些函数 CCN 同级但未列入、本次未动"。
- **确认目标文件能否 import。** 有的脚本顶部有 `try/except ImportError: sys.exit(1)` 守卫，依赖子模块未初始化时根本 import 不进来。做差分测试前先往 `sys.modules` 塞桩模块再加载。
- **先取旧版留底。** `git show <整改前commit>:<path> > old.py`，差分测试要用。

## 3. 拆分手法

### 3.1 按职责切，不按行数切

入口函数 41 → 6 的切法（六类职责各一个 helper）：

| 职责 | helper |
| --- | --- |
| 参数规整 | `_normalize_args` |
| 默认值填充 | `_apply_defaults` |
| 运行时配置校验 | `_validate_config` |
| 批量模式分发 | `_run_batch_mode` |
| 单项模式分发 | `_run_single_mode` |
| 单项报告写出 | `_write_report` |

批次执行函数 34 → 11 的切法更细：目录准备、日志挂载、任务配置解析、依赖未满足判定、跳过记录、日志头、任务执行、收尾强停、通过率格式化、YAML 汇总、CSV 报告各一个。

判据：**一个 helper 只回答一个问题**。如果 helper 的名字里出现"然后"，说明还能再切。

### 3.2 helper 返回窄类型 + 哨兵，调用方适配

不要在 helper 里返回调用方的布尔语义。让 helper 返回它自己自然的类型，失败给 `None`：

```python
def _upload_artifact(client, artifact_path: str, target_id: int) -> str | None:
    """上传构建产物（内容哈希去重）；成功返回 artifact_id，失败返回 None。"""
    try:
        resp = client.upload(artifact_path)
        ...
        return artifact_id
    except (requests.RequestException, OSError, ValueError) as e:
        logger.error(f"upload exception: {e}")
        return None
```

调用方把 `None` 翻成自己的返回语义：

```python
artifact_id = _upload_artifact(client, artifact_path, target_id)
if artifact_id is None:
    return False
```

这样 helper 保持纯粹、可独立测试，也不把调用方的错误约定渗进去。

### 3.3 用三态返回压平嵌套

轮询类逻辑最容易把 CCN 堆高。把"一次响应说明什么"抽成返回三态的函数：

```python
def _check_progress(process_resp: dict) -> bool | None:
    """判定一次进度响应：True=已完成，False=明确失败，None=仍在进行。"""
```

`None` 表示"还没结论，继续轮询"。轮询循环于是只剩一个 `if`，判定分支全部下沉。比原来层层嵌套的 if 链既浅又容易测。

### 3.4 跨函数重复一并合并

拆分时顺手消除重复，但只合并**已经存在**的重复，不预判：

- CSV 行推导在原实现里有两份（批次汇总一份、单项报告一份）→ 合并为 `_csv_row` + `_truncate` + `_CSV_HEADER`；
- 云配置判定与客户端构造散布在三个函数里 → 合并为 `_has_remote_config` + `_build_client`。

出现两次并不自动要求提取；但**拆分已经把它带到眼前**时，合并比留着便宜。

### 3.5 字面量集合提成模块级常量

```python
_SUCCESS_STATUSES = ("success", "succeed", "done", "installed", "finish")
_FAIL_STATUSES = ("fail", "failed", "error")
```

只提"表达业务枚举"的集合，不提显而易见的局部字面量。

## 4. 保真清单

纯行为保持重构，以下八项改动前后必须一致：

| # | 项 | 说明 |
| --- | --- | --- |
| 1 | **异常捕获类型按段保留** | 每一段的 `except` 类型逐个照搬，不因为"看起来能合并"就统一；合并异常段会改变控制流 |
| 2 | **序列化键集不能变** | 产出的 YAML / CSV / JSON 的键名、键序、键集合逐项一致 |
| 3 | **`.get(k, d)` 与 `.get(k) or d` 不等价** | 前者在值为 `0` / `""` / `False` / `None` 时返回该值，后者会 fallback 到 `d`。互换会改变边界行为 |
| 4 | **if 链判定顺序** | 分支顺序直接影响先命中哪条；重排即改行为 |
| 5 | **try 内赋值配 `continue` 防 `UnboundLocalError`** | 拆出 helper 后，原 try 内部分支赋值的变量在后续引用前可能未绑定 |
| 6 | **日志文案** | 日志文本是外部可观察行为（运维在查），一字不改 |
| 7 | **返回码与退出码** | `True/False/None`、`sys.exit(1)` 的时机照搬 |
| 8 | **副作用顺序** | 写文件、发请求、改全局状态的先后次序 |

## 5. 差分测试

**这是唯一被认可的验证方式。** 单测只能证明新代码符合你的理解；差分测试证明新代码和老代码一致。

做法：

1. 取旧版：`git show <base>:file.py > old.py`
2. 同输入跑两版，比对产物。可比的产物：CSV / YAML 文件字节、`--help` 全文、纯函数返回值、参数解析结果。
3. 涉及外部调用的流程，比对**返回值序列 + 调用序列**（用记录型 fake 捕获每一次调用及参数）。
4. 目标文件 import 不进来时，先往 `sys.modules` 塞桩模块（缺哪个塞哪个），再加载。

实测有效口径：批次报告 CSV **逐字节相同**，单项报告 CSV 相同，安装流程 **21 个场景**的返回值与调用序列一致。

覆盖不到的场景要如实说，不要用"逻辑等价"替代证据。

## 6. 提交信息

把证据写进 commit，不写"重构完成"：

```
refactor: 拆分三个高风险函数，圈复杂度 41/34/24 降至 6/11/3

- main: 41 -> 6，拆出 _normalize_args / _apply_defaults / ...
- run_batch: 34 -> 11，拆出 ...
- deploy_artifact: 24 -> 3，拆出 ...

同时消除三处跨函数重复：...

异常捕获类型、日志文案、批次汇总 YAML 键集均与原实现保持一致。
已用差分测试（旧版 vs 新版同输入比产物）验证：CSV 逐字节相同，
安装流程 21 个场景返回值与调用序列一致。
```

四个要素：**前后数字 / 拆了什么 / 哪些不变量被保持 / 用什么证据验证**。
