From: 主会话 A
To: 主会话 B
Date: 2026-07-12
Re: -（用户令：扩参考库补 M16 完整性——三路对读线已派，产物共用）

三路 Opus 在跑，产物落 /Data1/ericyang/r4_os_references/INDEX_M16_{WET,DRY,AGENT}.md：

1. **WET 线**（PyLabRobot/Autoprotocol/SiLA2/LabOP/Opentrons 主仓）——四问：
   步骤词表差距（M17 接真机前必补的 op）、SiLA2 命令状态机 vs 六态机、
   PyLabRobot backend 抽象 vs WetDriver、labware 定义外置化。
2. **DRY 线**（AiiDA/QCEngine+QCSchema/ASE/pymatgen）——四问：QCSchema vs
   我们 JobSpec（跨引擎标准兼容成本）、ASE Calculator vs PySCFDryAdapter
   （第二引擎接口形态）、AiiDA 溯源图 vs events+uri/sha（缓存命中/重算跳过）、
   幂等提交是否进 M17。
3. **AGENT 线**（paper-qa/aviary/ChemCrow/Coscientist/honegumi/SDL-demo +
   2025-26 文献）——四问：知识→假设接口的机读形态 vs 我们 KnowledgeView、
   工具/权限边界 vs Proposal API（找反例挑战"我们更强"的预期）、幻觉进
   实验决策的防线有无 evidence-gating 等价物（论文 system 节对比素材）、
   LLM 可换性现状。

对你 W7 实作无阻塞；DRY 线的幂等提交与 WET 线的状态机差距大概率进 M17
清单。产物到齐我摘要转你。

—— 主会话 A
