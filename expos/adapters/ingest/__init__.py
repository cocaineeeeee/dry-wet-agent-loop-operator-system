"""测量 ingestion 层（M3 实现）：一切来源统一产出 ObservationObject(trust=PENDING)。

本模块是唯一的 raw→observation 转换通道（sim_* / csv_loader / image 都走这里）。

设计边界（公理 2 / §6）：ingestion **只做形状对齐与布局映射，不做任何裁决**——
产出的观测一律 trust=PENDING、qc=None、routing=None、failure_attr=None、
next_action=None。裁决（QC→trust）在 qc/ 层，路由/归因在 lifecycle/models 层；
ingestion 不裁决、不归因、不路由。

红线：本层禁读真值 sidecar（truth_records / truth/），只消费 RawResult 的 OS 可见面。
"""

from __future__ import annotations

from expos.adapters.base import AdapterError, RawResult
from expos.kernel.objects import (
    ExperimentObject,
    InstrumentMeta,
    LayoutMeta,
    MaterialMeta,
    MeasuredResult,
    ObservationObject,
    RawDataRef,
    WellAssignment,
)


def raw_to_observations(
    exp: ExperimentObject,
    raw_results: list[RawResult],
    raw_kind: str = "sim",
) -> list[ObservationObject]:
    """把一批 RawResult 映射到布局并转成 ObservationObject（唯一转换入口）。

    响亮失败（一律 AdapterError）：
    - exp 无布局；
    - raw.well_id 不在 exp.layout（未知孔位不得静默丢弃）；
    - raw.metric 与 exp.objective.metric 不一致（未知指标必须响亮失败）；
    - raw 的 cand/control 归属与布局 WellAssignment 不一致。

    产出观测保持"待裁决"语义：trust=PENDING、qc=None、routing=None、
    failure_attr=None、next_action=None——ingestion 不裁决/归因/路由。
    """
    if exp.layout is None:
        raise AdapterError(f"exp {exp.exp_id} 无布局，无法 ingestion（需先 design.layout 分配）")

    by_well: dict[str, WellAssignment] = {w.well_id: w for w in exp.layout.wells}
    metric = exp.objective.metric
    observations: list[ObservationObject] = []

    for raw in raw_results:
        wa = by_well.get(raw.well_id)
        if wa is None:
            raise AdapterError(
                f"raw well_id {raw.well_id!r} 不在 exp {exp.exp_id} 的布局中（拒绝静默丢弃未知孔）"
            )
        if raw.metric != metric:
            raise AdapterError(
                f"well {raw.well_id}: raw.metric {raw.metric!r} 与 objective.metric "
                f"{metric!r} 不一致（未知指标必须响亮失败）"
            )
        if raw.cand_id != wa.cand_id or raw.control_id != wa.control_id:
            raise AdapterError(
                f"well {raw.well_id}: raw 归属 (cand={raw.cand_id}, control={raw.control_id}) "
                f"与布局 (cand={wa.cand_id}, control={wa.control_id}) 不一致"
            )

        is_control = wa.control_id is not None
        obs = ObservationObject(
            exp_id=exp.exp_id,
            round_id=exp.round_id,
            cand_id=wa.cand_id,
            control_id=wa.control_id,
            is_control=is_control,
            result=MeasuredResult(
                metric=metric,
                value=raw.value,
                secondary=dict(raw.secondary),
                unit=raw.unit,
            ),
            # Provenance survives ingestion (letter 051): uri/sha256 land on
            # raw_ref, engine on instrument_meta. RawDataRef.uri is str (default
            # ""), so a None source uri maps to "".
            raw_ref=RawDataRef(
                uri=raw.uri or "", kind=raw_kind, sha256=raw.sha256
            ),
            layout_meta=LayoutMeta(
                well_id=wa.well_id,
                row=wa.row,
                col=wa.col,
                is_edge=wa.is_edge,
                block_id=wa.block_id,
            ),
            material_meta=MaterialMeta(
                solution_batch=raw.solution_batch,
                additive_lot=raw.additive_lot,
            ),
            instrument_meta=InstrumentMeta(
                exposure=raw.exposure,
                illumination=raw.illumination,
                capture_index=raw.capture_index,
                engine=raw.engine,
            ),
            # 以下一律留默认——ingestion 不裁决/归因/路由：
            qc=None,
            failure_attr=None,
            routing=None,
            next_action=None,
        )
        observations.append(obs)

    return observations
