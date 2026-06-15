"""HTTP 层拆分模块（#16）：

把原本集中在 main.py 的 Pydantic schema、鉴权依赖等抽到独立模块，降低单文件集中度。
路由处理体目前仍在 main.py 的 create_app() 中（涉及限流/lifespan 闭包，需在可运行环境逐端点验证后再拆）。
"""
