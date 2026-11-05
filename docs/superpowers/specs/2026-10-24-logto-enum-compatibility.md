# S5-A3 真实 Logto 预检与枚举兼容修订

实际只读观察日期：2026-10-24（Asia/Shanghai）。沿用已批准的同 Logto 恢复、subject／应用 ID 保持和中央原权威账本路线，不改变身份归并、权限或资金合同。

## 已观测缺口

真实固定版 Logto 1.45.0 的 PostgreSQL 为 16.15，public schema 存在 8 种 native enum，被 9 个列使用。上一版 collector 将所有自定义类型拒绝，因此不能对这套真实库完成恢复证明。不是通过删除 enum 或 cast text 绕过，而是明确支持原生 enum 定义。

## 最小修订

- 支持被选 schema 内 native enum 以及 PostgreSQL 自动生成的同 schema enum[]，比较 type 的 schema／name／owner／ACL、generated array 对应关系与 owner／ACL，以及按顺序的完整 enum labels。
- 包括未被列使用和空的 enum；OID 和 enumsortorder 的具体浮点数不入摘要，因为逻辑恢复可以重新分配它们。标签相对顺序必须相同。
- 外部同名 enum、非自动 enum array、domain／range／composite 继续拒绝；不扩大成任意 PostgreSQL 类型兼容。
- 继续保留现有完整表／函数／触发器／RLS／序列／ACL、只读可见性、备份 SHA、上下文、policy、subject 集合和 freshness 检查。实际源库单次指纹不生成 proof，也不等同于目标已恢复或身份已迁移。

## 验证与交付

真实隔离 PostgreSQL RED→GREEN，覆盖等价不同 OID、sort-number 重编号、标签顺序／定义、未使用 enum、schema、array element、owner／ACL，以及仍拒绝的类型。PostgreSQL 禁止自动 array 单独 GRANT，按实际行为测试其拒绝和 element ACL 差异，不能篡改 pg_catalog 制造通过结果。

代码静态独立审查、完整中央回归、新 wheel／源码一致性与目标 Linux 干净安装分别记录。S5-A2 旧 wheel／ZIP 保留；新 enum 支持需使用本轮新包。真实源只读指纹与未来实际两库恢复证明、本人新 issuer 登录仍是独立阶段。
