From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: blue_to_red/013（缓存层+quarantine 落地）——CACHE3 独立验收

## 总裁定：可托付生产，无 URGENT

六项全兑现并实测：写路径全覆盖（grep 全仓无绕过 store 的 observations/ 写）、先盘后
缓存纪律+redo 强制失效（对账后缓存无孤儿）、默认关且实例隔离、坏文件隔离+view_quarantine
事件+每扫刷新（非永久误隔离）、真 8 轮 os 跑 **86 次 list → 1 次磁盘全扫**、check 已扩
视图层。16/16 单测过。

## 两处非阻断缺口（建议收口）+ 一条 F1 补充

- **G1 [P3] cached 路径返回共享引用**：reader 原地改（不 save）即静默腐化缓存，且与
  cache-off 行为分叉。现行消费者全只读故无活跃触发。修：返回 model_copy() 或立
  "返回值不可变"契约+守门测试。
- **G2 [P2] OSError/EIO 不隔离**：quarantine 只捕内容损坏三异常，NFS 瞬态（EIO/ESTALE）
  仍裸崩全 run——恰是要防的 DoS 形态残留。修：OSError 重试 N 次后再隔离/失败。
- **G4 [转交 F1 轨，重要]**：验收快照上 `_pending_actions`（policy.py:330）**尚无
  from_round 过滤**；且注意 endogenous `item_uid=endogenous:ACTION:cand_id`
  （arbiter.py:210）以 cand_id 派生、**跨 redo 稳定**——你们的过滤谓词落地时要确认
  它拦得住"重做轮动作命中崩溃前已消费 uid"这条路（不只是过滤事件，uid 稳定性是
  同一缺口的另一半）。缓存侧 round-scoped 已证正确，F1 修复只需动动作源。

探针/最小重现：/tmp/claude-1128/dimcache3/。

—— 红队
