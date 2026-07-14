"""RCGP-UCB 鲁棒响应模型（扩展臂，配置依据 REFERENCE_MAP §17.1 / arXiv 2311.00463
的 RCGP + arXiv 2511.15315 的 Plateau-IMQ 收紧；docs/ARCHITECTURE.md §8）。

M9 对照臂：对"幅度无界、频次有界"的观测腐败（离群/断传感器/极值）零成本鲁棒。
与 expos os 臂的对照点在于——**鲁棒性长在模型层，不长在路由层**：VerdictPolicy 侧
仍是 trust-blind 的 NaivePolicy，本类是 robust 家族成员（把腐败当"更噪的观测"软剪裁掉），
而非靠 TRUSTED 信任路由把可疑数据挡在门外。

数学（Altamirano et al. 2024, Prop. 3.1，闭式加权后验，纯 numpy 实现）：
    μ^R(x) = m(x) + k(x)ᵀ (K + σ² J_w)⁻¹ (y − m_w)
    Σ^R(x) = k(x,x) − k(x)ᵀ (K + σ² J_w)⁻¹ k(x)
    J_w = diag(σ²/2 · w⁻²)        →  σ² J_w = diag(σ⁴/2 · w⁻²)
    m_w = m + σ² ∇_y log(w²)      （outlier 收缩项，把离群点的等效目标拉回先验均值）
其中权重 w(x,y)=β·decay，β=σ/√2（论文式；使 plateau 内 σ²J_w=σ²I、m_w=m，
**精确退化为标准 GP** → 零成本鲁棒）。RCGP 无有效边际似然（score-matching 广义
Bayes 后验），sklearn 的 fit 不适用 → 超参用**加权 LOO-CV**（Sundararajan & Keerthi
的 O(n³) 解析式）小网格选。

Plateau-IMQ 权重（β=σ/√2，r=y−m(x)）：
    |r| ≤ L → w = β                         （常数平台：可信区，完全采信）
    |r| > L → w = β·(1+((|r|−L)/c)²)^(−1/2)   （IMQ 重尾软衰减：软剪裁离群）
IMQ 重尾（非 SE）→ 不把"略超阈"的点剪得太狠，只对极端腐败强衰减；有界 →
PIF 有界 → 论文 Prop 3.2 的鲁棒性保证。

依赖红线：只 import kernel.objects + design.space + sklearn.kernels/numpy——
不得 import adapters/qc/planner/agent/ui，不得触碰真值 sidecar（公理 6）。
"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
from sklearn.gaussian_process.kernels import Matern

from expos.design.space import dim, to_unit
from expos.errors import ExposError
from expos.kernel.objects import (
    DesignSpace,
    ExperimentObject,
    ObservationObject,
    Routing,
    TrustLevel,
)


class RobustGPError(ExposError):
    user_facing = False  # 收到未裁决观测/未训练取用=上游 bug，不许静默


# 小网格（n<300 全量重分解，成本可忽略、确定性）——单位立方 lengthscale、
# 相对稳健方差的信号方差与噪声方差比例。加密网格无收益，故取粗档（§17.1）。
_LENGTHSCALE_GRID = (0.1, 0.2, 0.35, 0.5, 0.75)
_SIGNALVAR_FACTORS = (0.5, 1.0, 2.0)
_NOISEVAR_FACTORS = (1e-3, 1e-2, 1e-1)


class RobustResponseModel:
    """RCGP-UCB 臂：与 ResponseModel 同接口（fit/predict/score_pool/snapshot 同形状，
    方便 loop 换装），但把鲁棒性放在模型层——离群点被 Plateau-IMQ 权重软剪裁，
    而非靠信任路由挡门。

    参数
    ----
    kappa: UCB 探索系数（score_pool = μ^R + kappa·σ^R，σ^R 为潜函数 std / f-std）。
    plateau_L: 平台半宽 L（残差单位；None → 从残差稳健分位数自适应）。
    imq_c: IMQ 软阈 c（None → 从残差稳健尺度自适应；论文 QAD 的稳健化）。
    """

    def __init__(
        self,
        space: DesignSpace,
        direction: str = "maximize",
        seed: int = 0,
        kappa: float = 2.0,
        plateau_L: float | None = None,
        imq_c: float | None = None,
    ):
        if direction not in ("maximize", "minimize"):
            raise RobustGPError(f"未知 direction: {direction!r}")
        self.space = space
        self.direction = direction
        self.kappa = float(kappa)
        self._seed = seed
        self._plateau_L = plateau_L
        self._imq_c = imq_c
        # 训练态
        self._X: np.ndarray | None = None
        self._y: np.ndarray | None = None
        self._m0: float = 0.0  # 稳健常数先验均值（中位数）——不被离群拉动
        self._L: float = 0.0
        self._c: float = 1.0
        self._length_scale: float = 0.3
        self._signal_var: float = 1.0
        self._noise_var: float = 1e-2  # σ²（内部方向单位²）
        self._alpha: np.ndarray | None = None  # (K + σ²J_w)⁻¹ (y − m_w)
        self._chol: np.ndarray | None = None  # A 的 Cholesky 下三角

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
    ) -> "RobustResponseModel":
        """只接受 TRUSTED + TO_RESPONSE_MODEL 的观测；其余一律 RobustGPError
        （守门与 ResponseModel 同款——可疑数据进不了响应模型是本类的类型行为，
        不是调用方纪律。robust 家族在模型层做剪裁，但结构性守门仍在）。

        ``per_point_alpha`` 为与 ResponseModel.fit 同形的接口参数（loop 统一调用）：
        rcgp 臂的稳健性长在模型层（Plateau-IMQ 权重软剪裁离群），观测噪声由内部
        加权后验自适应，故**逐点 alpha 被忽略**（rcgp 走 PassthroughAggregation，
        本就传 None）——保留形参只为与 loop 的第五注入点接口对齐。"""
        del per_point_alpha  # 接口对齐用；rcgp 模型层自适应噪声，不消费逐点 alpha
        if not observations:
            raise RobustGPError("训练集为空")
        lut = self._params_lookup(experiments)
        X_rows, y_vals = [], []
        for obs in observations:
            if obs.trust != TrustLevel.TRUSTED or obs.routing != Routing.TO_RESPONSE_MODEL:
                raise RobustGPError(
                    f"obs {obs.obs_id} trust={obs.trust.value}/routing="
                    f"{obs.routing.value if obs.routing else None} 不得进入响应模型（公理 2）"
                )
            if obs.result.value is None:
                raise RobustGPError(f"obs {obs.obs_id} 无测量值")
            entry_id = obs.cand_id if obs.cand_id is not None else obs.control_id
            if entry_id not in lut:
                raise RobustGPError(f"obs {obs.obs_id} 的条目 {entry_id!r} 无参数记录")
            X_rows.append(to_unit(self.space, lut[entry_id]))
            y_vals.append(float(obs.result.value))
        X = np.asarray(X_rows, dtype=float)
        y = np.asarray(y_vals, dtype=float)
        if self.direction == "minimize":
            y = -y

        self._X, self._y = X, y
        # 稳健常数先验均值：中位数不被离群拉动（论文强调先验均值须干净，
        # 否则会误判高信号点为离群——常数中位数天然免疫幅度腐败）。
        self._m0 = float(np.median(y))
        r = y - self._m0  # 残差（对先验均值）——离群点在此凸显为大幅残差

        # 稳健尺度（MAD）：3 个巨幅离群 < 50% 点数 → 中位数绝对偏差不受污染
        s_r = 1.4826 * float(np.median(np.abs(r - np.median(r))))
        s_r = max(s_r, 1e-9)
        ar = np.abs(r)
        # 平台半宽 L：默认取残差 |r| 的 0.75 分位（远低于离群频次 → 分位数干净），
        # 使绝大多数干净点落入平台（退化为标准 GP），离群点远在平台外。
        self._L = self._plateau_L if self._plateau_L is not None else float(np.quantile(ar, 0.75))
        # IMQ 软阈 c：稳健尺度（论文 QAD 的稳健化，避开 LOO 选 c 会去拟合离群的病态）
        self._c = self._imq_c if self._imq_c is not None else max(s_r, 1e-9)

        # 与 σ 无关的每点腐败结构（β=σ/√2 时 β 在下列量中约掉）：
        #   σ²J_w = σ² · infl，infl_i = 1（平台）| 1+u²（离群），u=(|r|−L)/c
        #   m_w   = m + σ² · shift_base，shift_base_i = ∇_y log(w²)（平台=0）
        infl, shift_base = self._corruption_terms(r)

        # 稳健信号方差基准（供网格缩放）
        sig0 = max((1.4826 * float(np.median(np.abs(y - np.median(y))))) ** 2, 1e-6)

        best = None  # (score, length_scale, signal_var, noise_var)
        for ls in _LENGTHSCALE_GRID:
            corr = self._matern_corr(X, X, ls)  # 相关阵（对角=1）
            for sf in _SIGNALVAR_FACTORS:
                sv = sf * sig0
                K = sv * corr
                for nf in _NOISEVAR_FACTORS:
                    nv = nf * sig0
                    score = self._weighted_loo_score(K, nv, infl, shift_base, y)
                    if best is None or score > best[0]:
                        best = (score, ls, sv, nv)
        _, self._length_scale, self._signal_var, self._noise_var = best

        # 以最优超参做最终分解，缓存 α 与 Cholesky 供预测
        self._factorize(infl, shift_base)
        return self

    def _corruption_terms(self, r: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Plateau-IMQ 的每点腐败结构（与 σ 无关）：
        返回 (infl, shift_base)——σ²J_w = σ²·infl，m_w = m + σ²·shift_base。"""
        ar = np.abs(r)
        out = ar > self._L
        infl = np.ones_like(r)
        shift = np.zeros_like(r)
        if np.any(out):
            u = (ar[out] - self._L) / self._c
            infl[out] = 1.0 + u**2
            # ∇_y log(w²) = −(2u/(1+u²))·sign(r)/c （β 常数项求导消失）
            shift[out] = -(2.0 * u / (1.0 + u**2)) * np.sign(r[out]) / self._c
        return infl, shift

    def _matern_corr(self, A: np.ndarray, B: np.ndarray, length_scale: float) -> np.ndarray:
        """Matérn(ν=2.5) 相关阵（仅借 sklearn 核对象算 K；对角=1，幅度另乘）。"""
        return Matern(length_scale=length_scale, nu=2.5)(A, B)

    def _noise_diag(self, infl: np.ndarray, noise_var: float) -> np.ndarray:
        return noise_var * infl  # (σ²J_w) 对角

    def _weighted_loo_score(
        self,
        K: np.ndarray,
        noise_var: float,
        infl: np.ndarray,
        shift_base: np.ndarray,
        y: np.ndarray,
    ) -> float:
        """加权 LOO-CV 对数预测密度（越大越好）。用 Sundararajan & Keerthi 的 O(n³)
        解析式（只需一次求逆）：Ainv=(K+σ²J_w)⁻¹，z=y−m_w，
            μ_i^LOO = m_i + z_i − (Ainv z)_i / Ainv_ii
            var_i   = 1/Ainv_ii − (σ²J_w)_ii + σ²      （对 y_i 的预测方差）
        目标按权重 w_i²（∝1/infl_i）归一 → 离群点不主导超参选择（"加权"LOO 的要义）。"""
        n = y.shape[0]
        noise = self._noise_diag(infl, noise_var)
        A = K + np.diag(noise)
        try:
            Ainv = np.linalg.inv(A)
        except np.linalg.LinAlgError:
            return -np.inf
        d = np.diag(Ainv)
        if np.any(d <= 0) or not np.all(np.isfinite(d)):
            return -np.inf
        m_w = self._m0 + noise_var * shift_base
        z = y - m_w
        Az = Ainv @ z
        mu_loo = self._m0 + z - Az / d
        var_loo = 1.0 / d - noise + noise_var
        var_loo = np.clip(var_loo, 1e-12, None)
        resid = y - mu_loo
        logpd = -0.5 * np.log(2.0 * np.pi * var_loo) - 0.5 * resid**2 / var_loo
        w_obj = 1.0 / infl  # ∝ w²（β 约掉）：离群点权重小 → 不主导目标
        w_obj = w_obj / (w_obj.sum() or 1.0)
        return float(np.sum(w_obj * logpd) * n)

    def _factorize(self, infl: np.ndarray, shift_base: np.ndarray) -> None:
        """以选定超参缓存 A 的 Cholesky 与 α=(K+σ²J_w)⁻¹(y−m_w)。"""
        X, y = self._X, self._y
        K = self._signal_var * self._matern_corr(X, X, self._length_scale)
        noise = self._noise_diag(infl, self._noise_var)
        A = K + np.diag(noise)
        jitter = 1e-10 * self._signal_var
        for _ in range(6):  # 数值兜底：必要时加抖动直至正定（确定性升阶）
            try:
                Lc = np.linalg.cholesky(A + jitter * np.eye(A.shape[0]))
                break
            except np.linalg.LinAlgError:
                jitter *= 10.0
        else:
            raise RobustGPError("加权后验矩阵非正定（数值失败）")
        m_w = self._m0 + self._noise_var * shift_base
        z = y - m_w
        alpha = np.linalg.solve(Lc.T, np.linalg.solve(Lc, z))
        self._chol = Lc
        self._alpha = alpha

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
            raise RobustGPError(f"维度不符: 期望 {dim(self.space)}, 得到 {arr.shape[1]}")
        return arr

    def _posterior(self, Xstar: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """返回 (μ^R, 潜函数方差)（内部方向）。"""
        if self._X is None:
            raise RobustGPError("模型未训练")
        Ks = self._signal_var * self._matern_corr(Xstar, self._X, self._length_scale)
        mu = self._m0 + Ks @ self._alpha
        # 潜函数方差 = k(x,x) − k*ᵀ A⁻¹ k*，A⁻¹k* 经 Cholesky 求解
        v = np.linalg.solve(self._chol, Ks.T)  # (n, m)
        var = self._signal_var - np.sum(v**2, axis=0)
        var = np.clip(var, 0.0, None)
        return mu, var

    def predict(self, x: "dict[str, Any] | np.ndarray") -> tuple[np.ndarray, np.ndarray]:
        """返回 (mean, std)，mean 已换回原方向；std 为 y-预测不确定度
        （潜函数方差 + 平台噪声 σ²）——与 ResponseModel.predict 语义对齐。"""
        mu, var = self._posterior(self._as_unit_matrix(x))
        if self.direction == "minimize":
            mu = -mu
        sd = np.sqrt(var + self._noise_var)
        return mu, sd

    def score_pool(self, pool: np.ndarray) -> np.ndarray:
        """UCB 采集分（内部最大化方向，越大越好）：μ^R + kappa·σ^R。
        σ^R 用潜函数 std（f-std，不含观测噪声）——与 ResponseModel 采集同构。"""
        if self._X is None:
            raise RobustGPError("模型未训练")
        pool = np.asarray(pool, dtype=float)
        if pool.ndim != 2 or pool.shape[1] != dim(self.space):
            raise RobustGPError(f"池维度不符: 期望 (n, {dim(self.space)}), 得到 {pool.shape}")
        mu, var = self._posterior(pool)
        return mu + self.kappa * np.sqrt(var)

    # ------------------------------------------------------------ 批量选点（KB）

    def _kb_posterior(
        self, X_aug: np.ndarray, y_aug: np.ndarray, Xstar: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """冻结超参（length_scale/signal_var/noise_var/m0/L/c）下，对增广数据重解
        加权后验，返回 (μ^R, 潜函数方差)——供 Kriging Believer 逐步 condition。"""
        r = y_aug - self._m0
        infl, shift = self._corruption_terms(r)
        K = self._signal_var * self._matern_corr(X_aug, X_aug, self._length_scale)
        A = K + np.diag(self._noise_var * infl)
        jitter = 1e-10 * self._signal_var
        for _ in range(6):
            try:
                Lc = np.linalg.cholesky(A + jitter * np.eye(A.shape[0]))
                break
            except np.linalg.LinAlgError:
                jitter *= 10.0
        else:
            raise RobustGPError("KB 增广后验矩阵非正定（数值失败）")
        z = y_aug - (self._m0 + self._noise_var * shift)
        alpha = np.linalg.solve(Lc.T, np.linalg.solve(Lc, z))
        Ks = self._signal_var * self._matern_corr(Xstar, X_aug, self._length_scale)
        mu = self._m0 + Ks @ alpha
        v = np.linalg.solve(Lc, Ks.T)
        var = np.clip(self._signal_var - np.sum(v**2, axis=0), 0.0, None)
        return mu, var

    def select_batch_kb(self, pool: np.ndarray, q: int) -> list[int]:
        """Kriging Believer 批量选点（与 ResponseModel.select_batch_kb 同接口）：迭代 q 次
        取池中 UCB（μ^R+kappa·σ^R）最优未选点，以其后验均值为伪观测 condition（冻结
        超参下对增广数据重解加权后验），重复。返回池内索引（长度 q，无重复）；不改 self 状态。
        注：rcgp 臂用 BaselinePlanner（走 score_pool），本方法为接口对齐/未来 planner 备用。"""
        if self._X is None:
            raise RobustGPError("模型未训练")
        pool = np.asarray(pool, dtype=float)
        if pool.ndim != 2 or pool.shape[1] != dim(self.space):
            raise RobustGPError(f"池维度不符: 期望 (n, {dim(self.space)}), 得到 {pool.shape}")
        n_pool = pool.shape[0]
        if n_pool == 0:
            raise RobustGPError("候选池为空")
        if not isinstance(q, int) or q < 1 or q > n_pool:
            raise RobustGPError(f"非法批量大小 q={q!r}（须 1≤q≤{n_pool}）")

        X_aug = self._X.copy()
        y_aug = self._y.copy()
        selected: list[int] = []
        for _ in range(q):
            mu, var = self._kb_posterior(X_aug, y_aug, pool)
            ucb = mu + self.kappa * np.sqrt(var)
            if selected:
                ucb[selected] = -np.inf
            idx = int(np.argmax(ucb))
            selected.append(idx)
            X_aug = np.vstack([X_aug, pool[idx]])
            y_aug = np.append(y_aug, mu[idx])
        return selected

    # ------------------------------------------------------------ 指纹

    def snapshot(self) -> str:
        """训练集内容指纹：同数据同配置 → 同指纹；数据变 → 指纹变
        （排序键含 (X, y) 联合，行序无关且对重复行确定，与 ResponseModel 同款）。"""
        h = hashlib.sha256()
        h.update(f"{self.direction}|{dim(self.space)}|rcgp-matern2.5".encode())
        if self._X is not None:
            rows = np.hstack([self._X, self._y.reshape(-1, 1)])
            order = np.lexsort(rows.T)
            h.update(np.ascontiguousarray(rows[order]).tobytes())
        return h.hexdigest()[:16]
