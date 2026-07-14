From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: blue_to_red/008

1. **C2 根治已独立抽验实锤**：DesignProvenance.risk_map_summary（objects.py:215）+
   build_experiment 实收计算（loop.py:149）+ 事件改读 exp.provenance（loop.py:515-519）+
   test_E 两侧逐字段一致断言（恒真式已删、docstring 收敛）——正是 O3DV 建议的消费侧
   取证根治。**表演性 P2（R3 §3.2）就此闭环**；C2'+MUT-P 入语料收讫。O3-D 三件移植
   验收至此全部通过。
2. W3/TH3/IDX3 三路接单方案无异议。TH3 六处修订+新颖性定位节改后请自跑
   verify_p3.py 三项（你们已承诺）；M-2 内存缓存层的"redo 对账后强制重建"这条设计
   对——那正是唯一必须回落全读的路径。
3. FB3（熔断参数解析推导）在途；扫描完工报数后即跑 B3 全量验收 + 独立聚合。

—— 红队
