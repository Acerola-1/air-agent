## 新增需求

### 需求：统一事件路由分发
Java 服务 SHALL 根据事件类型将 SSE 事件分发到对应的处理方法。

#### 场景：分发 messages 事件
- **WHEN** 收到 `event: messages` 类型的 SSE 事件
- **THEN** 调用 `handleMessagesEvent()` 方法处理

#### 场景：分发 custom 事件
- **WHEN** 收到 `event: custom` 类型的 SSE 事件
- **THEN** 调用 `handleCustomEvent()` 方法处理

#### 场景：分发 updates 事件
- **WHEN** 收到 `event: updates` 类型的 SSE 事件
- **THEN** 调用 `handleUpdatesEvent()` 方法处理

#### 场景：分发 metadata 事件
- **WHEN** 收到 `event: metadata` 类型的 SSE 事件
- **THEN** 调用 `handleMetadataEvent()` 方法处理

#### 场景：忽略未知事件类型
- **WHEN** 收到未知类型的 SSE 事件
- **THEN** 记录警告日志
- **AND** 返回空 Flux，不中断流处理

### 需求：事件处理顺序保证
Java 服务 SHALL 保证事件按接收顺序处理。

#### 场景：顺序处理事件
- **WHEN** 连续收到多个 SSE 事件
- **THEN** 按接收顺序依次处理
- **AND** 不并发处理同一对话的事件
