"""响应模型：sklearn GP 包装（配置依据 REFERENCE_MAP §11.1，docs/ARCHITECTURE.md §8）。

公理 2/3 的模型侧执行面：
- **只吃显式选入训练集的观测**，且结构性拒绝 trust != TRUSTED 或
  routing != TO_RESPONSE_MODEL 的观测（fit 直接 ModelError）——
  可疑数据进不了响应模型不是调用方纪律，是本类的类型行为；
- 方向内部统一为最大化（minimize 域在内部翻符号）；
- snapshot() 返回训练集指纹（内容哈希），写入 provenance 供审计复现。

依赖红线：只 import kernel.objects + design.space + sklearn/numpy——
不得 import adapters/qc/planner/agent/ui，不得触碰真值 sidecar（公理 6）。
"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel

from expos.design.space import dim, to_unit
from expos.kernel.objects import (
    DesignSpace,
    ExperimentObject,
    ObservationObject,
    Routing,
    TrustLevel,
)


from expos.errors import ExposError


class ModelError(ExposError):
    user_facing = False  # 收到未裁决观测=上游 bug，不许静默



class ResponseModel:
    """§11.1 默认配置：ConstantKernel × Matern(ν=2.5, 收紧 bounds) + WhiteKernel，
    normalize_y=True，alpha=1e-8 数值抖动，n_restarts 默认 10（研究记录建议 ~20，
    此处为闭环内每轮重训的速度折中，可经参数上调——记录于 CHECKPOINTS M4）。"""

    def __init__(
        self,
        space: DesignSpace,
        direction: str = "maximize",
        seed: int = 0,
        kappa: float = 2.0,
        n_restarts: int = 10,
        ard: bool = True,
    ):
        if direction not in ("maximize", "minimize"):
            raise ModelError(f"未知 direction: {direction!r}")
        self.space = space
        self.direction = direction
        self.kappa = float(kappa)
        self._seed = seed
        self._n_restarts = n_restarts
        # ard=True（默认，naive/os/robust 行为零变化）：逐维各向异性 length_scale（d 个
        # 自由超参）。ard=False：单标量 length_scale（各向同性，1 个自由超参）——os-lite
        # 容量对齐臂专用，把本模型降到与 rcgp（RobustResponseModel 各向同性 45 格 LOO）
        # 同容量档，隔离"路由层贡献"于"代理容量税"（见 loop._rcgp_capacity_model_factory）。
        self._ard = ard
        self._gp = self._make_gp(per_point_alpha=None)
        self._X: np.ndarray | None = None
        self._y: np.ndarray | None = None
        self._noise_var_y: float = 0.0

    def _make_gp(self, per_point_alpha: np.ndarray | None) -> GaussianProcessRegressor:
        """默认：Matérn + WhiteKernel（学噪声）+ 微小 alpha 抖动。
        给定逐点 alpha（副本方差）时：**去掉 WhiteKernel**（共存会双重计噪，
        且 predict 的 std 会被同方差项污染——sklearn 源码走读 §13.10 指令），
        噪声全部走 alpha 对角（不进 kernel.diag → predict std 即 f-std）。"""
        d = dim(self.space)
        # ard=True → 逐维 length_scale 向量（各向异性）；ard=False → 单标量（各向同性，
        # os-lite 容量对齐档，与 rcgp 各向同性同容量）。
        length_scale = np.full(d, 0.3) if self._ard else 0.3
        matern = ConstantKernel(1.0, (1e-2, 1e2)) * Matern(
            length_scale=length_scale,
            length_scale_bounds=(1e-2, 1e2),  # 默认 (1e-5,1e5) 小样本易退化（§11.1）
            nu=2.5,
        )
        if per_point_alpha is None:
            kernel = matern + WhiteKernel(noise_level=1e-2, noise_level_bounds=(1e-4, 1e0))
            # 下界取 1e-4（§11.1 建议 1e-3）：域噪声方差 4e-4 会被 1e-3 顶在边界
            alpha = 1e-8
        else:
            kernel = matern
            alpha = per_point_alpha  # 已含抖动（fit 中处理）
        return GaussianProcessRegressor(
            kernel=kernel,
            normalize_y=True,
            alpha=alpha,
            n_restarts_optimizer=self._n_restarts,
            random_state=self._seed,
        )

    # ------------------------------------------------------------ 训练

    @staticmethod
    def _params_lookup(experiments: list[ExperimentObject]) -> dict[str, dict[str, Any]]:
        lut: dict[str, dict[str, Any]] = {}
        for exp in experiments:
            for c in exp.candidates:
                lut[c.cand_id] = c.params
            for c in exp.controls:
                lut[c.control_id] = c.params
        return lut

    def fit(
        self,
        observations: list[ObservationObject],
        experiments: list[ExperimentObject],
        per_point_alpha: "np.ndarray | None" = None,
    ) -> "ResponseModel":
        """Admission gate (VNext batch-1, letter 047 design point (b)):

        - TRUSTED + TO_RESPONSE_MODEL: always admitted (certification-grade data).
        - SUSPECT + QUARANTINE: admitted for LEARNING ONLY, and only when an
          explicit ``per_point_alpha`` vector is supplied -- the weight vector IS
          the learning-policy admission record (soft-trust downweighting travels
          as an explicit parameter, never as a mutated synthetic copy). The
          persisted object keeps its QUARANTINE trust/routing untouched: learning
          admission does not alter certification state.
        - Everything else: ModelError.

        per_point_alpha: per-observation noise variance (original y units^2,
        1:1 with observations); when given, switches to alpha-diagonal noise GP
        (no WhiteKernel)."""
        if not observations:
            raise ModelError("训练集为空")
        if per_point_alpha is not None and len(per_point_alpha) != len(observations):
            raise ModelError(
                f"per_point_alpha 长度 {len(per_point_alpha)} 与观测数 {len(observations)} 不符"
            )
        lut = self._params_lookup(experiments)
        X_rows, y_vals = [], []
        for obs in observations:
            _soft_admitted = (
                per_point_alpha is not None
                and obs.trust == TrustLevel.SUSPECT
                and obs.routing == Routing.QUARANTINE
            )
            if not _soft_admitted and (
                obs.trust != TrustLevel.TRUSTED or obs.routing != Routing.TO_RESPONSE_MODEL
            ):
                raise ModelError(
                    f"obs {obs.obs_id} trust={obs.trust.value}/routing="
                    f"{obs.routing.value if obs.routing else None} 不得进入响应模型（公理 2）"
                )
            if obs.result.value is None:
                raise ModelError(f"obs {obs.obs_id} 无测量值")
            entry_id = obs.cand_id if obs.cand_id is not None else obs.control_id
            if entry_id not in lut:
                raise ModelError(f"obs {obs.obs_id} 的条目 {entry_id!r} 无参数记录")
            X_rows.append(to_unit(self.space, lut[entry_id]))
            y_vals.append(float(obs.result.value))
        X = np.asarray(X_rows, dtype=float)
        y = np.asarray(y_vals, dtype=float)
        if self.direction == "minimize":
            y = -y

        # 规范化行序（R1-5(c) 根因修复）：训练集是集合语义，行序不改模型数学，但
        # L-BFGS 超参优化的浮点求和顺序随行序有 ~1e-12 抖动，闭环里会被 UCB 近平局
        # argmax 放大成选点分叉（同 seed 重跑/续跑不等价）。固定 (X, y[, alpha])
        # 联合字典序 → fit 对观测输入顺序**严格不变**（逐位确定）。
        alpha_arr: np.ndarray | None = None
        if per_point_alpha is not None:
            alpha_arr = np.clip(np.asarray(per_point_alpha, dtype=float), 0.0, None)
            sort_key = np.hstack([X, y.reshape(-1, 1), alpha_arr.reshape(-1, 1)])
        else:
            sort_key = np.hstack([X, y.reshape(-1, 1)])
        order = np.lexsort(sort_key.T)
        X, y = X[order], y[order]

        if alpha_arr is not None:
            # sklearn 的 alpha 作用在 normalize_y 后的空间——原单位方差须除以 y_var
            # （§13.10：normalize_y 不缩放 alpha，是已知的语义细节）
            y_std = float(y.std()) or 1.0
            alpha_scaled = alpha_arr[order] / (y_std**2) + 1e-8
            self._gp = self._make_gp(per_point_alpha=alpha_scaled)
        else:
            self._gp = self._make_gp(per_point_alpha=None)

        self._gp.fit(X, y)
        self._X, self._y = X, y
        # 学到的观测噪声方差换算回原 y 单位（sklearn 走读结论：predict 的 std 含
        # WhiteKernel 噪声=y-不确定度；normalize_y 下 y_var 已乘 _y_train_std²，
        # 故扣噪要用 noise_level·y_std²）。alpha 模式下噪声不进 kernel.diag，
        # predict std 天然是 f-std → 扣噪量为 0。
        try:
            noise_norm = float(self._gp.kernel_.k2.noise_level)
        except AttributeError:  # alpha 模式（无 WhiteKernel）或核结构变化：不扣噪
            noise_norm = 0.0
        y_train_std = float(getattr(self._gp, "_y_train_std", 1.0))
        self._noise_var_y = noise_norm * y_train_std**2
        return self

    @property
    def n_train(self) -> int:
        return 0 if self._y is None else int(self._y.shape[0])

    # ------------------------------------------------------------ 预测与采集

    def _as_unit_matrix(self, x: "dict[str, Any] | np.ndarray") -> np.ndarray:
        if isinstance(x, dict):
            return to_unit(self.space, x).reshape(1, -1)
        arr = np.asarray(x, dtype=float)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        if arr.shape[1] != dim(self.space):
            raise ModelError(f"维度不符: 期望 {dim(self.space)}, 得到 {arr.shape[1]}")
        return arr

    def predict(self, x: "dict[str, Any] | np.ndarray") -> tuple[np.ndarray, np.ndarray]:
        """返回 (mean, std)，mean 已换回原方向。
        注意：std 是 **y 的不确定度**（含学到的观测噪声——sklearn 语义，
        WhiteKernel 参与 predict 的 diag）；采集内部用扣噪的 f-std。"""
        if self._X is None:
            raise ModelError("模型未训练")
        mu, sd = self._gp.predict(self._as_unit_matrix(x), return_std=True)
        if self.direction == "minimize":
            mu = -mu
        return mu, sd

    def _f_std(self, y_std_arr: np.ndarray) -> np.ndarray:
        """y-std → f-std（潜函数不确定度）：扣掉观测噪声方差。
        采集用 f-std——否则 UCB 在高噪区把"可复现的噪声"误当探索价值
        （sklearn 源码走读 finding，REFERENCE_MAP §13.10）。"""
        return np.sqrt(np.clip(y_std_arr**2 - self._noise_var_y, 0.0, None))

    def score_pool(self, pool: np.ndarray) -> np.ndarray:
        """UCB 采集分（内部最大化方向，越大越好）——供 design.sampler.propose_candidates
        作 score_fn。σ 用 f-std（扣观测噪声）；kappa 由调用方按轮次调度（§11.1）。"""
        if self._X is None:
            raise ModelError("模型未训练")
        mu, sd = self._gp.predict(np.asarray(pool, dtype=float), return_std=True)
        return mu + self.kappa * self._f_std(sd)

    # ------------------------------------------------------------ 批量选点（KB）

    @staticmethod
    def _kb_noise_var(gp: GaussianProcessRegressor) -> float:
        """副本 GP 的观测噪声方差（原 y 单位）——与 fit 的换算同构：
        白噪声模式取冻结的 WhiteKernel.noise_level·_y_train_std²；alpha 对角模式
        噪声不进 kernel.diag → predict std 即 f-std，扣噪量为 0。"""
        try:
            noise_norm = float(gp.kernel_.k2.noise_level)
        except AttributeError:
            noise_norm = 0.0
        return noise_norm * float(getattr(gp, "_y_train_std", 1.0)) ** 2

    def _kb_refit(
        self, X_aug: np.ndarray, y_aug: np.ndarray, alpha_aug: "float | np.ndarray"
    ) -> GaussianProcessRegressor:
        """冻结超参下对增广数据集全量重分解（§13.9 最终建议：n<300 成本可忽略、
        比增量 Schur 补更稳，避免 pending 密集时开负根出 nan）。冻结姿势（§13.10 Q4）：
        clone 已 fit 的 kernel_（携学到的超参）+ optimizer=None → 重 fit 只重解不重训。"""
        from sklearn.base import clone

        gp = GaussianProcessRegressor(
            kernel=clone(self._gp.kernel_),
            normalize_y=True,
            alpha=alpha_aug,
            optimizer=None,
            n_restarts_optimizer=0,
            random_state=self._seed,
        )
        gp.fit(X_aug, y_aug)
        return gp

    def select_batch_kb(self, pool: np.ndarray, q: int) -> list[int]:
        """Kriging Believer 批量选点：迭代 q 次——用当前（含伪观测）后验算 UCB
        （μ + kappa·f_std，f_std 语义与 score_pool 一致），选池中最优未选点，
        以其后验均值为伪观测 condition（sklearn 路线：clone(kernel_)+optimizer=None
        重 fit 增广数据集，全量重分解——n<300 成本可忽略，§13.9 最终建议），
        重复。返回池内索引（长度 q，无重复）。未训练/池空/q>len(pool) → ModelError。
        确定性：同模型同池同 q → 同索引序列。
        注意：伪观测追加不改 self 状态（在副本 GP 上做，方法结束后模型不变——
        测试断言 snapshot 前后一致）。"""
        if self._X is None:
            raise ModelError("模型未训练")
        pool = np.asarray(pool, dtype=float)
        if pool.ndim != 2 or pool.shape[1] != dim(self.space):
            raise ModelError(f"池维度不符: 期望 (n, {dim(self.space)}), 得到 {pool.shape}")
        n_pool = pool.shape[0]
        if n_pool == 0:
            raise ModelError("候选池为空")
        if not isinstance(q, int) or q < 1 or q > n_pool:
            raise ModelError(f"非法批量大小 q={q!r}（须 1≤q≤{n_pool}）")

        # 副本状态：增广数据集（内部方向单位）+ 逐点噪声。白噪声模式噪声走冻结的
        # WhiteKernel（全点含伪观测同尺度）→ alpha 保持标量抖动；per-point 模式噪声
        # 走 alpha 对角 → 伪观测 alpha 取现有 alpha 的中位数（§规格）。
        base_alpha = self._gp.alpha
        per_point = isinstance(base_alpha, np.ndarray)
        pseudo_alpha = float(np.median(base_alpha)) if per_point else None

        X_aug = self._X.copy()
        y_aug = self._y.copy()
        gp = self._gp
        noise_var = self._noise_var_y
        selected: list[int] = []
        for _ in range(q):
            mu, sd = gp.predict(pool, return_std=True)
            f_std = np.sqrt(np.clip(sd**2 - noise_var, 0.0, None))
            ucb = mu + self.kappa * f_std  # 内部最大化方向
            if selected:
                ucb[selected] = -np.inf  # 不重复选
            idx = int(np.argmax(ucb))
            selected.append(idx)
            # 伪观测 = 冻结超参下的后验均值（内部单位；均值不变仅方差收缩，
            # 取 μ+β·σ 会漂移成 Constant-Liar——§13.9）
            X_aug = np.vstack([X_aug, pool[idx]])
            y_aug = np.append(y_aug, mu[idx])
            if per_point:
                alpha_aug = np.concatenate(
                    [base_alpha, np.full(len(selected), pseudo_alpha)]
                )
            else:
                alpha_aug = base_alpha
            gp = self._kb_refit(X_aug, y_aug, alpha_aug)
            noise_var = self._kb_noise_var(gp)
        return selected

    # ------------------------------------------------------------ 指纹

    def snapshot(self) -> str:
        """模型状态指纹：训练集内容 + **拟合出的核超参**；任一变 → 指纹变。

        - 排序键含 (X, y) 联合——闭环里副本与哨兵产生大量相同 X 行、y 各异，
          只按 X 排序对重复行会随输入顺序断序（对抗审查 finding，已修）。
        - 超参（R1-5(c) 修复）：只哈希 (X, y) 对"训练数据相同、拟合态不同"盲——
          resume 重建与一次跑完若 GP 拟合发散，审计层看不见。此处纳入 fit 后的
          `kernel_.theta`（log 空间超参向量：ConstantKernel/length_scale/WhiteKernel）
          + alpha 模式标记（scalar/per_point，两模式核结构不同）。
        - 浮点容差权衡：theta 先 round 到 1e-10 再哈希。同机同 BLAS 下确定性重拟合
          逐位一致，1e-10 远低于超参的物理意义精度、又足以吸收打印/序列化级噪声；
          跨平台 BLAS 差异可能超过该容差——指纹本就应把"拟合态不可复现"暴露出来，
          故刻意不放宽（宁可假阳性报发散，不静默吞真发散）。"""
        h = hashlib.sha256()
        h.update(f"{self.direction}|{dim(self.space)}|matern2.5".encode())
        if self._X is not None:
            rows = np.hstack([self._X, self._y.reshape(-1, 1)])
            order = np.lexsort(rows.T)  # 以 (X, y) 全列为键 → 行序无关且对重复行确定
            h.update(np.ascontiguousarray(rows[order]).tobytes())
            theta = np.round(np.asarray(self._gp.kernel_.theta, dtype=float), 10)
            h.update(b"|theta:")
            h.update(np.ascontiguousarray(theta).tobytes())
            mode = b"per_point" if isinstance(self._gp.alpha, np.ndarray) else b"scalar"
            h.update(b"|alpha_mode:" + mode)
        return h.hexdigest()[:16]
