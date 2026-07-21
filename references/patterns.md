# 附录 C、通用模式库（从实践中抽象）

> 本文件是 `dev-standards` skill 的按需参考,对应原「附录 C」。需要某个模式时再翻。
> 注意:以下多为 TypeScript 示例,套用到具体子项目时按该项目实际语言/栈调整。

## C.1 模块测试模式

```
测试金字塔:E2E（少量）→ 集成测试（中量）→ 单元测试（大量）
```

- **Mock 工厂**:用 `makeMockClient()` / `makeConfig(overrides)` 创建测试对象
- **测试隔离**:`beforeEach` 用 fake timers,`afterEach` 恢复
- **定时器测试**:`vi.useFakeTimers()` + `vi.advanceTimersByTime()`

## C.2 配置管理模式

```typescript
// Zod schema 验证 + 自动默认值
export const ConfigSchema = z.object({
  wsUrl: z.string().url().optional(),
  requireMention: z.boolean().default(true),
  maxPayloadBytes: z.number().int().positive().default(5000),
});

const config = ConfigSchema.parse({});  // 未提供的字段自动填默认值
// 热重载:fs.watch + safeParse（校验失败时保留旧配置,不崩溃）
```

## C.3 日志脱敏模式

```typescript
// ID 脱敏:123456789 → 123***
function maskId(id: string | number | undefined | null, visiblePrefix = 3): string
// URL 脱敏:保留 protocol + host,截断 pathname,隐藏 query
function maskUrl(url: string, maxPathLength = 40): string
// 文本中所有 5-12 位数字 ID 自动脱敏
function maskIdsInText(text: string): string
```

## C.4 重试机制模式

```typescript
interface RetryOptions {
  maxRetries: number;       // 默认 3
  baseDelayMs: number;      // 默认 200,后续 2x 指数增长
  shouldRetry: (error: unknown) => boolean;
}
// withRetry(fn, opts?) — 指数退避重试
// isRetryableError(error) — 5xx/网络错误可重试,4xx 不重试
```

## C.5 缓存模式

```typescript
// TTL 缓存:Map + 定期清理 + dispose()
class TtlCache<K, V> {
  get(key: K): V | null;
  set(key: K, value: V, ttlMs?): void;
  dispose(): void;
}
// 并发控制:cacheReady 标志防止重复加载
```

## C.6 状态管理模式

```typescript
// 状态机:idle → active → cooldown → idle
type State = "idle" | "active" | "cooldown" | "done";
class StateMachine {
  isAllowed(key: string, cooldownMs: number): boolean;
  markActive(key: string): void;
  markDone(key: string): void;    // active → cooldown
  markSilent(key: string): void;  // 直接清除
}

// 哨兵模式:防止并发操作,带超时防死锁
class SentinelGuard {
  acquire(key: string): boolean;  // 已有哨兵返回 false
  release(key: string): void;
  has(key: string): boolean;
}
```

## C.7 防抖模式

```typescript
interface DebounceOptions {
  windowMs: number;    // 防抖窗口（默认 1500ms）
  maxWaitMs: number;   // 最大等待（默认 8000ms）
  separator: string;   // 合并分隔符
}
// Debouncer: deliver(item) → 合并后 flush → executor(merged)
```

## C.8 依赖注入模式

```typescript
// 依赖接口 + 工厂函数
interface HandlerDeps {
  getClient: (accountId: string) => ApiClient | undefined;
  knownEntityIds: Set<string>;
  featureFlags: FeatureFlagManager;
}
// 测试时注入 mock:makeDeps(overrides)
// 模块 mock:vi.mock() 在 import 之前
```

## C.9 生命周期管理模式

```typescript
// 资源管理器:initialize → get → dispose → destroy
class ResourceManager {
  initialize(id: string, config: ResourceConfig): void;
  get(id: string): Resource | undefined;
  dispose(id: string): void;
  disposeAll(): void;
  destroy(): void;  // 清理定时器 + 释放所有资源
}
// 启动编排:验证配置 → 初始化 → 注册 → 连接 → 等待退出 → 清理
```

## C.10 错误处理模式

```typescript
// 两类错误:BusinessError（已知可恢复）vs SystemError（未知不可恢复）
class BusinessError extends Error { code: string; statusCode: number; }
class SystemError extends Error { cause?: Error; }

// 错误传播:BusinessError → warn + 返回错误信息
//           SystemError → error(exc_info) + 返回通用信息
//           未知 → error(exc_info) + "Unknown error"
// 优雅降级:try/catch 返回安全默认值,不影响主流程
```

## C.11 模式选择指南

| 场景     | 推荐模式         | 理由                           |
| -------- | ---------------- | ------------------------------ |
| 配置验证 | Schema 验证模式  | 类型安全、自动默认值、易于测试 |
| 日志输出 | 日志脱敏模式     | 防止敏感信息泄露               |
| 网络请求 | 重试机制模式     | 提高可靠性、防止雪崩           |
| 频繁读取 | TTL 缓存模式     | 减少 I/O、提高性能             |
| 状态管理 | 状态机模式       | 清晰的状态流转、易于测试       |
| 高频触发合并 | 防抖模式         | 合并突发调用、降低下游压力     |
| 单元测试 | 依赖注入模式     | 易于 mock、测试隔离            |
| 资源管理 | 生命周期管理模式 | 防止资源泄漏、优雅关闭         |
| 错误处理 | 错误分类模式     | 区分业务/系统错误、合理降级    |

## C.12 数据库事务模式

```
事务边界 = 业务用例边界,不在 DAO 内部跨 method。
withTransaction(async (tx) => {
  const order = await tx.order.create(...);
  await tx.inventory.decrement(...);
});
幂等性 key:(userId, businessKey) 唯一约束防重复。隔离级别按一致性要求选。
```

## C.13 CLI 参数解析模式

```typescript
const args = parseArgs(process.argv.slice(2));
const config = ArgsSchema.parse(args);  // 失败 → 友好错误 + 退出码 2
```

退出码:0=成功,1=运行时错,2=参数错。`--json` 模式:日志走 stderr,主输出 stdout（管道友好）。配置优先级:环境变量 > CLI flag > 默认值。

## C.14 批处理流水线模式

```
阶段:读 → 转换 → 写,每阶段幂等。
checkpoint 写入 .processed/,崩溃后跳过。
backpressure:消费侧满时主动 sleep。失败批次 → dead_letter/,不阻塞主流程。
```

## C.15 幂等性 key 模式

```typescript
const idempotencyKey = `${userId}:${businessKey}:${windowId}`;
// 首次写入成功 → 标记;后续 → 返回上次结果
```

写操作必须带幂等性 key（防止网络重试导致重复扣款）。key 设计:业务侧稳定 + 时间窗可丢弃。
