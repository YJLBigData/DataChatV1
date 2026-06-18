"""按域拆分的 HTTP 路由模块（APIRouter）。

main.py 原本把 ~50 个端点全塞在一个 1492 行的 create_app() 闭包里；这里按域拆成独立
APIRouter，create_app() 改为一行 include_router 挂载（与 expert_team/smartq/exports 同构）。
每个路由模块自包含：只依赖 app.api.deps（鉴权）/ app.api.schemas（入参）/ 各 core store /
app.api.support（跨域共享的可信结果与会话落地等纯函数），与 create_app 闭包解耦。
"""
