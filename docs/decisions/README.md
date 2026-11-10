# 决策记录

每条记录一个已经做出的决定：背景、决定、影响。新决定按编号追加；推翻旧决定时新增一条并在旧记录注明“已被 NNNN 取代”，不改写原文。

| 编号 | 决定 |
| --- | --- |
| [0001](0001-freeze-b2-b3-av.md) | 冻结 B2/B3 与 AV 深度工作，改用标准备份 |
| [0002](0002-workspace-outside-temp.md) | 工作区与构建输入不得放在系统临时目录 |
| [0003](0003-signed-in-app-updates.md) | 应用内更新：离线签名清单 + 受管安装布局 |
| [0004](0004-web-routes-by-domain.md) | 按领域拆分 web/app.py |
| [0005](0005-b3-tests-per-process.md) | B3 测试逐文件单独进程运行 |
| [0006](0006-media-maintenance-optional.md) | 媒体维护许可改为可选，默认关闭 |
| [0007](0007-media-writeoffs.md) | 媒体任务也走审计过的平台承担（迁移 021） |
| [0008](0008-native-mac-window.md) | 桌面版用 Mac 原生窗口 |
| [0009](0009-product-name-youchuang.md) | 产品对外名称改为优创，内部标识不变 |
| [0010](0010-owner-allowance.md) | 付款关闭期间的所有者额度（迁移 022） |
| [0011](0011-console-owner-allowance.md) | 后台显示用户名和邮箱，管理员可在网页发放固定所有者额度（迁移 023、024） |
| [0012](0012-console-user-management.md) | 后台停用账号、强制下线、备注和标签、按套餐发放（迁移 025） |
| [0013](0013-redemption-codes.md) | 兑换码（一次一码、只存摘要、每批每钱包一次）和网页审批运营者（迁移 026） |
| [0014](0014-subscription-catalog.md) | 中央统一管理模型，套餐决定模型权限、总点数和倍率（迁移 027） |
| [0015](0015-console-provider-configs.md) | 六类接口都在后台配置，Key 加密保存（迁移 028） |
| [0016](0016-console-supplier-platforms.md) | 后台每一类接口可选第三方平台（迁移 029、030） |
| [0017](0017-console-provider-accounts.md) | 同一平台可接入多个账号，后台改成新建接入（迁移 032） |
| [0018](0018-plan-edit-and-retire.md) | 套餐可以编辑（发布新版本）、复制、停用和恢复，不硬删除（迁移 036） |
| [0019](0019-plan-payment-periods.md) | 每个套餐有按年、按月、一次性三种价格；付款关闭时也展示价格 |
| [0020](0020-console-user-admin.md) | 后台管理用户：新建、编辑、软删除、管理员角色、调整余额、更换和结束套餐 |
