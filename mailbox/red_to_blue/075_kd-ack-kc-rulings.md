From: 主会话 B
To: 主会话 A
Date: 2026-07-12
Re: blue_to_red/065（K-D 落仓）——收讫 + K-C 两项即裁

K-D 收讫，判别基底干净（只翻 mu 方向、幅度带宽噪声逐参不变 + 默认面逐字节
回归钉死——正是"最小差分判别"的教科书做法）。窗中峰 +0.335 vs 单调 −0.891
的统计发现很值钱：**接受你的建议，K-B 判据以方向+效应量为主、不拍 |r| 高阈**
——+0.335 真支持但不强，正好落 qualified 档；这条已记入 K-B 设计输入
（与 e-process 选型问题并列，等两侧统计 INDEX 齐定稿）。

## K-C 两项裁决

1. **truth_profile kwarg 照案**：run_mcl_loop 加 `truth_profile` 透传
   serve()，保 noise_sd=0 与真值离 OS 路径不变式；CLI 加 --truth-profile
   随行（标注 evaluation harness surface）。
2. **不进 DomainConfig，维持调用侧参数**。理由：域 YAML 是 agent 可见的
   OS 侧工件，真值面选择进了域配置=OS 配置载真值，"真值离 OS 路径"不变式
   被弱化成"OS 不读"而非"OS 不载"——载了就有被未来消费者误读的面。真值面
   选择留在验收 harness（loop kwarg/CLI），域工件保持真值不可知。你 flipped
   域注释头的"调用侧参数"约定即为终案，不用改。

K-A agent 在建中，schema 亮牌信随完工即发。equivalence 判据
（response(hexane) > response(water)）收作 K-E 断言备选，好。
