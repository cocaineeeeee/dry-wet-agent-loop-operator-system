"""expos 异常公共基类（设计依据：九仓库错误分类学横向调研，REFERENCE_MAP §13.12）。

- `user_facing=True`（默认）：领域错误——配置错、预算超支、布局不可行等，
  CLI 捕获后干净退出（exit 2 + 一行消息）；
- `user_facing=False`：内部不变量破坏（如模型层收到未裁决观测）——多半是 bug，
  CLI **不得**吞掉，保留响亮 traceback（"bug 不许静默"红线）。

可重试性**不**挂在异常上：九仓库无一例外把它编码在状态/生命周期层
（fireworks FIZZLED、plumpy ALLOWED 集、aiida ExitCode）——expos 由
ExpStatus + TrustLevel/Routing 承载，异常层再加会形成两套真相。
"""


class ExposError(Exception):
    user_facing: bool = True
